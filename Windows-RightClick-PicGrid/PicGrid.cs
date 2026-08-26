using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

namespace PicGridApp
{
    internal static class Program
    {
        internal const string Version = "1.3.2";
        internal const string IpcWindowTitle = "PicGrid_IPC_7E4F5A91_v131";
        internal const string MutexName = @"Local\PicGrid_SingleInstance_7E4F5A91_v131";
        internal static readonly string AppDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PicGrid");
        internal static readonly string LogPath = Path.Combine(AppDir, "picgrid.log");

        [STAThread]
        private static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Directory.CreateDirectory(AppDir);

            bool createdNew;
            using (Mutex mutex = new Mutex(true, MutexName, out createdNew))
            {
                if (!createdNew)
                {
                    IpcClient.Forward(args);
                    return;
                }

                try
                {
                    Logger.Write("Start v=" + Version + " primary=true args=" + args.Length);
                    Application.Run(new IpcForm(args));
                }
                catch (Exception ex)
                {
                    Logger.Write("Fatal: " + ex);
                    MessageBox.Show("拼图工具发生错误。\r\n\r\n" + ex.Message + "\r\n\r\n日志：" + LogPath,
                        "拼图工具", MessageBoxButtons.OK, MessageBoxIcon.Error);
                }
                finally
                {
                    try { mutex.ReleaseMutex(); } catch { }
                }
            }
        }
    }

    internal static class Logger
    {
        internal static void Write(string text)
        {
            try
            {
                Directory.CreateDirectory(Program.AppDir);
                File.AppendAllText(Program.LogPath,
                    DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "  " + text + Environment.NewLine,
                    new UTF8Encoding(true));
            }
            catch { }
        }
    }

    internal static class NativeMethods
    {
        internal const int WM_COPYDATA = 0x004A;
        internal const uint SMTO_ABORTIFHUNG = 0x0002;
        internal const int SW_RESTORE = 9;
        internal const uint SWP_NOMOVE = 0x0002;
        internal const uint SWP_NOSIZE = 0x0001;
        internal const uint SWP_SHOWWINDOW = 0x0040;
        internal static readonly IntPtr HWND_TOPMOST = new IntPtr(-1);
        internal static readonly IntPtr HWND_NOTOPMOST = new IntPtr(-2);

        [StructLayout(LayoutKind.Sequential)]
        internal struct COPYDATASTRUCT
        {
            public IntPtr dwData;
            public int cbData;
            public IntPtr lpData;
        }

        [DllImport("user32.dll", CharSet = CharSet.Unicode)]
        internal static extern IntPtr FindWindow(string lpClassName, string lpWindowName);

        [DllImport("user32.dll", SetLastError = true)]
        internal static extern bool SendMessageTimeout(IntPtr hWnd, uint Msg, IntPtr wParam, ref COPYDATASTRUCT lParam,
            uint fuFlags, uint uTimeout, out IntPtr lpdwResult);

        [DllImport("user32.dll")]
        internal static extern bool AllowSetForegroundWindow(uint dwProcessId);

        [DllImport("user32.dll")]
        internal static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);

        [DllImport("user32.dll")]
        internal static extern IntPtr GetForegroundWindow();

        [DllImport("kernel32.dll")]
        internal static extern uint GetCurrentThreadId();

        [DllImport("user32.dll")]
        internal static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);

        [DllImport("user32.dll")]
        internal static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        internal static extern bool BringWindowToTop(IntPtr hWnd);

        [DllImport("user32.dll")]
        internal static extern IntPtr SetActiveWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        internal static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

        [DllImport("user32.dll")]
        internal static extern bool SetWindowPos(IntPtr hWnd, IntPtr hWndInsertAfter, int X, int Y, int cx, int cy, uint uFlags);

        [DllImport("shlwapi.dll", CharSet = CharSet.Unicode)]
        internal static extern int StrCmpLogicalW(string psz1, string psz2);
    }

    internal static class IpcClient
    {
        internal static void Forward(string[] args)
        {
            if (args == null || args.Length == 0) return;

            IntPtr hwnd = IntPtr.Zero;
            for (int i = 0; i < 50 && hwnd == IntPtr.Zero; i++)
            {
                hwnd = NativeMethods.FindWindow(null, Program.IpcWindowTitle);
                if (hwnd == IntPtr.Zero) Thread.Sleep(40);
            }
            if (hwnd == IntPtr.Zero)
            {
                Logger.Write("Secondary could not find primary IPC window.");
                return;
            }

            uint pid;
            NativeMethods.GetWindowThreadProcessId(hwnd, out pid);
            try { NativeMethods.AllowSetForegroundWindow(pid); } catch { }

            for (int i = 0; i < args.Length; i++)
            {
                string s = args[i];
                if (String.IsNullOrWhiteSpace(s)) continue;
                SendString(hwnd, s);
            }
        }

        private static void SendString(IntPtr hwnd, string text)
        {
            byte[] bytes = Encoding.Unicode.GetBytes(text + "\0");
            IntPtr ptr = Marshal.AllocHGlobal(bytes.Length);
            try
            {
                Marshal.Copy(bytes, 0, ptr, bytes.Length);
                NativeMethods.COPYDATASTRUCT cds = new NativeMethods.COPYDATASTRUCT();
                cds.dwData = new IntPtr(1);
                cds.cbData = bytes.Length;
                cds.lpData = ptr;
                IntPtr result;
                NativeMethods.SendMessageTimeout(hwnd, NativeMethods.WM_COPYDATA, IntPtr.Zero, ref cds,
                    NativeMethods.SMTO_ABORTIFHUNG, 1500, out result);
            }
            finally
            {
                Marshal.FreeHGlobal(ptr);
            }
        }
    }

    internal sealed class IpcForm : Form
    {
        private readonly List<string> incoming = new List<string>();
        private readonly object sync = new object();
        private DateTime lastIncomingUtc;
        private DateTime firstIncomingUtc;
        private readonly System.Windows.Forms.Timer debounceTimer;
        private bool accepting = true;
        private bool workStarted;

        internal IpcForm(string[] initialArgs)
        {
            Text = Program.IpcWindowTitle;
            ShowInTaskbar = false;
            FormBorderStyle = FormBorderStyle.FixedToolWindow;
            StartPosition = FormStartPosition.Manual;
            Location = new Point(-32000, -32000);
            Size = new Size(1, 1);
            Opacity = 0.0;

            AddIncoming(initialArgs);

            debounceTimer = new System.Windows.Forms.Timer();
            debounceTimer.Interval = 80;
            debounceTimer.Tick += DebounceTimer_Tick;
            debounceTimer.Start();
        }

        protected override void OnShown(EventArgs e)
        {
            base.OnShown(e);
            Hide();
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == NativeMethods.WM_COPYDATA)
            {
                try
                {
                    NativeMethods.COPYDATASTRUCT cds = (NativeMethods.COPYDATASTRUCT)Marshal.PtrToStructure(
                        m.LParam, typeof(NativeMethods.COPYDATASTRUCT));
                    string s = Marshal.PtrToStringUni(cds.lpData);
                    if (!String.IsNullOrWhiteSpace(s)) AddIncoming(new string[] { s });
                    m.Result = new IntPtr(1);
                    return;
                }
                catch (Exception ex)
                {
                    Logger.Write("IPC receive error: " + ex.Message);
                }
            }
            base.WndProc(ref m);
        }

        private void AddIncoming(string[] args)
        {
            if (!accepting || args == null) return;
            lock (sync)
            {
                for (int i = 0; i < args.Length; i++)
                {
                    if (!String.IsNullOrWhiteSpace(args[i])) incoming.Add(args[i]);
                }
                if (incoming.Count > 0)
                {
                    if (firstIncomingUtc == DateTime.MinValue) firstIncomingUtc = DateTime.UtcNow;
                    lastIncomingUtc = DateTime.UtcNow;
                }
            }
        }

        private void DebounceTimer_Tick(object sender, EventArgs e)
        {
            if (workStarted) return;
            DateTime last;
            DateTime first;
            int count;
            lock (sync)
            {
                last = lastIncomingUtc;
                first = firstIncomingUtc;
                count = incoming.Count;
            }
            if (count == 0) return;

            double quietMs = (DateTime.UtcNow - last).TotalMilliseconds;
            double totalMs = (DateTime.UtcNow - first).TotalMilliseconds;
            // EXE 小实例启动很快；最后一个文件到达后静默 700ms 即认为本次多选收集完成。
            // 6 秒上限避免极端 Shell 状态下无限等待。
            if (quietMs >= 700.0 || totalMs >= 6000.0)
            {
                workStarted = true;
                accepting = false;
                debounceTimer.Stop();
                BeginInvoke(new MethodInvoker(RunWork));
            }
        }

        private void RunWork()
        {
            try
            {
                List<string> raw;
                lock (sync) raw = new List<string>(incoming);
                List<string> paths = ImageUtil.NormalizeAndSort(raw);
                Logger.Write("Collected raw=" + raw.Count + " valid=" + paths.Count);

                if (paths.Count < 2)
                {
                    ForegroundHelper.ForceMessageBox("请至少选择 2 张图片。", "拼图工具", MessageBoxIcon.Information);
                    Close();
                    return;
                }

                List<ImageInfo> infos = ImageUtil.LoadInfos(paths);
                Logger.Write("Readable images=" + infos.Count);
                if (infos.Count < 2)
                {
                    ForegroundHelper.ForceMessageBox("可读取的图片不足 2 张。", "拼图工具", MessageBoxIcon.Warning);
                    Close();
                    return;
                }

                using (SettingsForm dlg = new SettingsForm(infos))
                {
                    DialogResult result = dlg.ShowDialog();
                    if (result == DialogResult.OK)
                    {
                        SettingsData settings = dlg.ResultSettings;
                        Cursor.Current = Cursors.WaitCursor;
                        try
                        {
                            string output = Composer.Compose(infos, settings);
                            Logger.Write("Compose success output=" + output);
                            if (settings.OpenAfter && !String.IsNullOrEmpty(output))
                            {
                                try
                                {
                                    ProcessStartInfo psi = new ProcessStartInfo();
                                    psi.FileName = "explorer.exe";
                                    psi.Arguments = "/select,\"" + output + "\"";
                                    psi.UseShellExecute = true;
                                    Process.Start(psi);
                                }
                                catch (Exception ex) { Logger.Write("Explorer select failed: " + ex.Message); }
                            }
                        }
                        finally { Cursor.Current = Cursors.Default; }
                    }
                }
            }
            catch (Exception ex)
            {
                Logger.Write("RunWork error: " + ex);
                ForegroundHelper.ForceMessageBox(
                    "拼图失败。\r\n\r\n" + ex.Message + "\r\n\r\n日志：" + Program.LogPath,
                    "拼图工具", MessageBoxIcon.Error);
            }
            finally
            {
                Close();
            }
        }
    }

    internal static class ForegroundHelper
    {
        internal static void Force(Form form)
        {
            if (form == null || !form.IsHandleCreated) return;
            IntPtr hwnd = form.Handle;
            IntPtr foreground = NativeMethods.GetForegroundWindow();
            uint fgPid;
            uint fgThread = foreground == IntPtr.Zero ? 0 : NativeMethods.GetWindowThreadProcessId(foreground, out fgPid);
            uint curThread = NativeMethods.GetCurrentThreadId();
            bool attached = false;
            try
            {
                if (fgThread != 0 && fgThread != curThread)
                {
                    attached = NativeMethods.AttachThreadInput(curThread, fgThread, true);
                }
                NativeMethods.ShowWindow(hwnd, NativeMethods.SW_RESTORE);
                NativeMethods.SetWindowPos(hwnd, NativeMethods.HWND_TOPMOST, 0, 0, 0, 0,
                    NativeMethods.SWP_NOMOVE | NativeMethods.SWP_NOSIZE | NativeMethods.SWP_SHOWWINDOW);
                NativeMethods.BringWindowToTop(hwnd);
                NativeMethods.SetForegroundWindow(hwnd);
                NativeMethods.SetActiveWindow(hwnd);
                form.Activate();
            }
            finally
            {
                if (attached) NativeMethods.AttachThreadInput(curThread, fgThread, false);
            }
        }

        internal static void ForceMessageBox(string text, string caption, MessageBoxIcon icon)
        {
            using (Form owner = new Form())
            {
                owner.ShowInTaskbar = false;
                owner.StartPosition = FormStartPosition.CenterScreen;
                owner.Size = new Size(1, 1);
                owner.Opacity = 0;
                owner.Show();
                owner.TopMost = true;
                Force(owner);
                MessageBox.Show(owner, text, caption, MessageBoxButtons.OK, icon);
                owner.Close();
            }
        }
    }

    internal sealed class ImageInfo
    {
        public string Path;
        public int Width;
        public int Height;
        public double Ratio;
    }

    internal static class ImageUtil
    {
        private static readonly HashSet<string> Supported = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        { ".jpg", ".jpeg", ".jfif", ".png", ".bmp", ".gif", ".tif", ".tiff" };

        internal static List<string> NormalizeAndSort(List<string> raw)
        {
            List<string> result = new List<string>();
            HashSet<string> seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            for (int i = 0; i < raw.Count; i++)
            {
                string p = raw[i];
                try
                {
                    if (String.IsNullOrWhiteSpace(p)) continue;
                    p = p.Trim().Trim('"');
                    p = Path.GetFullPath(p);
                    if (!File.Exists(p)) continue;
                    string ext = Path.GetExtension(p);
                    if (!Supported.Contains(ext)) continue;
                    if (seen.Add(p)) result.Add(p);
                }
                catch { }
            }
            result.Sort(delegate(string a, string b)
            {
                return NativeMethods.StrCmpLogicalW(Path.GetFileName(a), Path.GetFileName(b));
            });
            return result;
        }

        internal static List<ImageInfo> LoadInfos(List<string> paths)
        {
            List<ImageInfo> infos = new List<ImageInfo>();
            for (int i = 0; i < paths.Count; i++)
            {
                try
                {
                    using (Image img = Image.FromFile(paths[i]))
                    {
                        int w = img.Width;
                        int h = img.Height;
                        int o = GetOrientation(img);
                        if (o >= 5 && o <= 8)
                        {
                            int t = w; w = h; h = t;
                        }
                        if (w <= 0 || h <= 0) continue;
                        ImageInfo info = new ImageInfo();
                        info.Path = paths[i];
                        info.Width = w;
                        info.Height = h;
                        info.Ratio = (double)w / (double)h;
                        infos.Add(info);
                    }
                }
                catch (Exception ex)
                {
                    Logger.Write("Skip unreadable: " + paths[i] + " ; " + ex.Message);
                }
            }
            return infos;
        }

        internal static int GetOrientation(Image image)
        {
            try
            {
                const int OrientationId = 0x0112;
                if (Array.IndexOf(image.PropertyIdList, OrientationId) >= 0)
                {
                    PropertyItem p = image.GetPropertyItem(OrientationId);
                    if (p.Value != null && p.Value.Length >= 2) return BitConverter.ToUInt16(p.Value, 0);
                    if (p.Value != null && p.Value.Length >= 1) return p.Value[0];
                }
            }
            catch { }
            return 1;
        }

        internal static void ApplyOrientation(Image image)
        {
            int o = GetOrientation(image);
            switch (o)
            {
                case 2: image.RotateFlip(RotateFlipType.RotateNoneFlipX); break;
                case 3: image.RotateFlip(RotateFlipType.Rotate180FlipNone); break;
                case 4: image.RotateFlip(RotateFlipType.Rotate180FlipX); break;
                case 5: image.RotateFlip(RotateFlipType.Rotate90FlipX); break;
                case 6: image.RotateFlip(RotateFlipType.Rotate90FlipNone); break;
                case 7: image.RotateFlip(RotateFlipType.Rotate270FlipX); break;
                case 8: image.RotateFlip(RotateFlipType.Rotate270FlipNone); break;
            }
        }
    }

    internal enum AdaptMode { Width, Height }

    internal sealed class SettingsData
    {
        public int Columns = 4;
        public AdaptMode Mode = PicGridApp.AdaptMode.Width;
        public double Scale = 0.5;
        public int Spacing = 6;
        public int Margin = 6;
        public string Format = "JPG";
        public bool OpenAfter = true;
    }

    internal static class SettingsStore
    {
        private const string KeyPath = @"Software\PicGrid";

        internal static SettingsData Load()
        {
            SettingsData s = new SettingsData();
            bool gotRegistry = false;
            try
            {
                using (RegistryKey key = Registry.CurrentUser.OpenSubKey(KeyPath))
                {
                    if (key != null)
                    {
                        gotRegistry = true;
                        s.Columns = ReadInt(key, "Columns", 4, 1, 30);
                        string mode = Convert.ToString(key.GetValue("AdaptMode", "Width"));
                        s.Mode = String.Equals(mode, "Height", StringComparison.OrdinalIgnoreCase) ? AdaptMode.Height : AdaptMode.Width;
                        s.Spacing = ReadInt(key, "Spacing", 6, 0, 200);
                        s.Margin = ReadInt(key, "Margin", 6, 0, 300);
                        string fmt = Convert.ToString(key.GetValue("Format", "JPG"));
                        s.Format = String.Equals(fmt, "PNG", StringComparison.OrdinalIgnoreCase) ? "PNG" : "JPG";
                        s.OpenAfter = ReadInt(key, "OpenAfter", 1, 0, 1) != 0;
                    }
                }
            }
            catch { }

            if (!gotRegistry) TryMigrateV12Json(s);
            s.Scale = 0.5; // 按需求：每次打开固定默认 0.5×，不记忆上次倍率。
            return s;
        }

        private static int ReadInt(RegistryKey key, string name, int def, int min, int max)
        {
            try
            {
                int v = Convert.ToInt32(key.GetValue(name, def));
                if (v < min) v = min;
                if (v > max) v = max;
                return v;
            }
            catch { return def; }
        }

        private static void TryMigrateV12Json(SettingsData s)
        {
            try
            {
                string path = Path.Combine(Program.AppDir, "settings.json");
                if (!File.Exists(path)) return;
                string json = File.ReadAllText(path, Encoding.UTF8);
                Match m;
                m = Regex.Match(json, "\\\"Columns\\\"\\s*:\\s*(\\d+)");
                if (m.Success) s.Columns = Math.Max(1, Math.Min(30, Int32.Parse(m.Groups[1].Value)));
                m = Regex.Match(json, "\\\"AdaptMode\\\"\\s*:\\s*\\\"(Width|Height)\\\"", RegexOptions.IgnoreCase);
                if (m.Success) s.Mode = String.Equals(m.Groups[1].Value, "Height", StringComparison.OrdinalIgnoreCase) ? AdaptMode.Height : AdaptMode.Width;
                m = Regex.Match(json, "\\\"Spacing\\\"\\s*:\\s*(\\d+)");
                if (m.Success) s.Spacing = Math.Max(0, Math.Min(200, Int32.Parse(m.Groups[1].Value)));
                m = Regex.Match(json, "\\\"Margin\\\"\\s*:\\s*(\\d+)");
                if (m.Success) s.Margin = Math.Max(0, Math.Min(300, Int32.Parse(m.Groups[1].Value)));
                m = Regex.Match(json, "\\\"Format\\\"\\s*:\\s*\\\"(JPG|PNG)\\\"", RegexOptions.IgnoreCase);
                if (m.Success) s.Format = m.Groups[1].Value.ToUpperInvariant();
                m = Regex.Match(json, "\\\"OpenAfter\\\"\\s*:\\s*(true|false)", RegexOptions.IgnoreCase);
                if (m.Success) s.OpenAfter = String.Equals(m.Groups[1].Value, "true", StringComparison.OrdinalIgnoreCase);
            }
            catch { }
        }

        internal static void Save(SettingsData s)
        {
            try
            {
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(KeyPath))
                {
                    key.SetValue("Columns", s.Columns, RegistryValueKind.DWord);
                    key.SetValue("AdaptMode", s.Mode == AdaptMode.Height ? "Height" : "Width", RegistryValueKind.String);
                    key.SetValue("Spacing", s.Spacing, RegistryValueKind.DWord);
                    key.SetValue("Margin", s.Margin, RegistryValueKind.DWord);
                    key.SetValue("Format", s.Format, RegistryValueKind.String);
                    key.SetValue("OpenAfter", s.OpenAfter ? 1 : 0, RegistryValueKind.DWord);
                }
            }
            catch (Exception ex) { Logger.Write("Save settings failed: " + ex.Message); }
        }
    }

    internal sealed class RowLayout
    {
        public int Start;
        public int Count;
        public double Height;
        public double Width;
        public double[] Widths;
    }

    internal sealed class LayoutData
    {
        public AdaptMode Mode;
        public int Width;
        public int Height;
        public List<RowLayout> Rows;
        public int Columns;
        public int RowsCount;
        public double[] ColWidths;
        public double[] RowHeights;
        public double[] ItemHeights;
    }

    internal static class GridLayoutEngine
    {
        internal static LayoutData GetLayout(List<ImageInfo> infos, SettingsData s)
        {
            int cols = Math.Min(Math.Max(1, s.Columns), infos.Count);
            int gap = Math.Max(0, s.Spacing);
            int margin = Math.Max(0, s.Margin);
            double scale = Math.Max(0.05, s.Scale);
            return s.Mode == AdaptMode.Height
                ? GetHeightAdaptive(infos, cols, gap, margin, scale)
                : GetWidthAdaptive(infos, cols, gap, margin, scale);
        }

        private static LayoutData GetWidthAdaptive(List<ImageInfo> infos, int columns, int gap, int margin, double scale)
        {
            List<RowLayout> rows = new List<RowLayout>();
            for (int start = 0; start < infos.Count; start += columns)
            {
                int count = Math.Min(columns, infos.Count - start);
                double sumH = 0;
                for (int j = 0; j < count; j++) sumH += infos[start + j].Height;
                double rowH = Math.Max(1.0, Math.Round((sumH / count) * scale));
                double[] widths = new double[count];
                double rowW = 0;
                for (int j = 0; j < count; j++)
                {
                    double rw = Math.Max(1.0, Math.Round(infos[start + j].Ratio * rowH));
                    widths[j] = rw;
                    rowW += rw;
                }
                if (count > 1) rowW += (count - 1) * gap;
                RowLayout row = new RowLayout();
                row.Start = start; row.Count = count; row.Height = rowH; row.Width = rowW; row.Widths = widths;
                rows.Add(row);
            }
            double contentW = 1;
            double contentH = 0;
            for (int i = 0; i < rows.Count; i++)
            {
                if (rows[i].Width > contentW) contentW = rows[i].Width;
                contentH += rows[i].Height;
            }
            if (rows.Count > 1) contentH += (rows.Count - 1) * gap;
            LayoutData l = new LayoutData();
            l.Mode = AdaptMode.Width;
            l.Rows = rows;
            l.Width = (int)Math.Ceiling(contentW + 2 * margin);
            l.Height = (int)Math.Ceiling(contentH + 2 * margin);
            return l;
        }

        private static LayoutData GetHeightAdaptive(List<ImageInfo> infos, int columns, int gap, int margin, double scale)
        {
            int rowsCount = (int)Math.Ceiling(infos.Count / (double)columns);
            double[] colWidths = new double[columns];
            for (int c = 0; c < columns; c++)
            {
                double sumW = 0; int count = 0;
                for (int r = 0; r < rowsCount; r++)
                {
                    int idx = r * columns + c;
                    if (idx < infos.Count) { sumW += infos[idx].Width; count++; }
                }
                colWidths[c] = count > 0 ? Math.Max(1.0, Math.Round((sumW / count) * scale)) : 1.0;
            }

            double[] rowHeights = new double[rowsCount];
            double[] itemHeights = new double[infos.Count];
            for (int r = 0; r < rowsCount; r++)
            {
                double rh = 1;
                for (int c = 0; c < columns; c++)
                {
                    int idx = r * columns + c;
                    if (idx >= infos.Count) continue;
                    double ih = Math.Max(1.0, Math.Round(colWidths[c] / infos[idx].Ratio));
                    itemHeights[idx] = ih;
                    if (ih > rh) rh = ih;
                }
                rowHeights[r] = rh;
            }

            double contentW = 0;
            for (int c = 0; c < columns; c++) contentW += colWidths[c];
            if (columns > 1) contentW += (columns - 1) * gap;
            double contentH = 0;
            for (int r = 0; r < rowsCount; r++) contentH += rowHeights[r];
            if (rowsCount > 1) contentH += (rowsCount - 1) * gap;

            LayoutData l = new LayoutData();
            l.Mode = AdaptMode.Height;
            l.Columns = columns;
            l.RowsCount = rowsCount;
            l.ColWidths = colWidths;
            l.RowHeights = rowHeights;
            l.ItemHeights = itemHeights;
            l.Width = (int)Math.Ceiling(contentW + 2 * margin);
            l.Height = (int)Math.Ceiling(contentH + 2 * margin);
            return l;
        }

        internal static bool IsTooLarge(LayoutData layout)
        {
            long pixels = (long)layout.Width * (long)layout.Height;
            return layout.Width > 30000 || layout.Height > 30000 || pixels > 120000000L;
        }
    }

    internal sealed class SettingsForm : Form
    {
        private readonly List<ImageInfo> infos;
        private NumericUpDown numCols;
        private RadioButton radWidth;
        private RadioButton radHeight;
        private NumericUpDown numScale;
        private NumericUpDown numSpace;
        private NumericUpDown numMargin;
        private ComboBox cmbFormat;
        private CheckBox chkOpen;
        private Label lblExample;
        private Label lblOutput;
        private Button btnOK;
        private bool initialized;
        internal SettingsData ResultSettings { get; private set; }

        internal SettingsForm(List<ImageInfo> infos)
        {
            this.infos = infos;
            BuildUi();
        }

        private void BuildUi()
        {
            SettingsData s = SettingsStore.Load();
            Text = "拼图设置  ·  已选 " + infos.Count + " 张";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            ClientSize = new Size(472, 428);
            Font = new Font("Microsoft YaHei UI", 9.0f, FontStyle.Regular, GraphicsUnit.Point);

            Label lblCols = NewLabel("每排图片：", 24, 24, 90, 24);
            Controls.Add(lblCols);
            numCols = new NumericUpDown();
            numCols.Location = new Point(120, 20); numCols.Size = new Size(90, 26);
            numCols.Minimum = 1; numCols.Maximum = 30; numCols.Value = Math.Min(30, Math.Max(1, s.Columns));
            Controls.Add(numCols);

            GroupBox grp = new GroupBox();
            grp.Text = "适应方式"; grp.Location = new Point(24, 60); grp.Size = new Size(424, 82);
            Controls.Add(grp);
            radWidth = new RadioButton();
            radWidth.Text = "宽度自适应（同一行同高，宽度按比例）";
            radWidth.Location = new Point(16, 24); radWidth.AutoSize = true;
            radWidth.Checked = s.Mode != AdaptMode.Height; grp.Controls.Add(radWidth);
            radHeight = new RadioButton();
            radHeight.Text = "高度自适应（同一列同宽，高度按比例）";
            radHeight.Location = new Point(16, 50); radHeight.AutoSize = true;
            radHeight.Checked = s.Mode == AdaptMode.Height; grp.Controls.Add(radHeight);

            Controls.Add(NewLabel("分辨率倍率：", 24, 161, 90, 24));
            numScale = new NumericUpDown();
            numScale.Location = new Point(120, 157); numScale.Size = new Size(90, 26);
            numScale.DecimalPlaces = 2; numScale.Increment = 0.05M; numScale.Minimum = 0.05M; numScale.Maximum = 4.00M;
            numScale.Value = 0.50M; Controls.Add(numScale);

            Button btnHalf = new Button(); btnHalf.Text = "0.5×"; btnHalf.Location = new Point(224, 156); btnHalf.Size = new Size(64, 28); Controls.Add(btnHalf);
            Button btnFull = new Button(); btnFull.Text = "1.0×"; btnFull.Location = new Point(296, 156); btnFull.Size = new Size(64, 28); Controls.Add(btnFull);
            btnHalf.Click += delegate { numScale.Value = 0.50M; };
            btnFull.Click += delegate { numScale.Value = 1.00M; };

            lblExample = NewLabel("", 120, 188, 328, 22); lblExample.ForeColor = Color.DimGray; Controls.Add(lblExample);

            Controls.Add(NewLabel("图片间距：", 24, 226, 90, 24));
            numSpace = new NumericUpDown(); numSpace.Location = new Point(120, 222); numSpace.Size = new Size(90, 26);
            numSpace.Minimum = 0; numSpace.Maximum = 200; numSpace.Value = Math.Min(200, Math.Max(0, s.Spacing)); Controls.Add(numSpace);

            Controls.Add(NewLabel("外边距：", 240, 226, 74, 24));
            numMargin = new NumericUpDown(); numMargin.Location = new Point(320, 222); numMargin.Size = new Size(90, 26);
            numMargin.Minimum = 0; numMargin.Maximum = 300; numMargin.Value = Math.Min(300, Math.Max(0, s.Margin)); Controls.Add(numMargin);

            Controls.Add(NewLabel("输出格式：", 24, 269, 90, 24));
            cmbFormat = new ComboBox(); cmbFormat.DropDownStyle = ComboBoxStyle.DropDownList;
            cmbFormat.Location = new Point(120, 265); cmbFormat.Size = new Size(90, 26); cmbFormat.Items.Add("JPG"); cmbFormat.Items.Add("PNG");
            cmbFormat.SelectedItem = s.Format == "PNG" ? "PNG" : "JPG"; Controls.Add(cmbFormat);

            chkOpen = new CheckBox(); chkOpen.Text = "完成后在文件夹中选中结果"; chkOpen.Location = new Point(240, 267); chkOpen.AutoSize = true;
            chkOpen.Checked = s.OpenAfter; Controls.Add(chkOpen);

            Panel infoPanel = new Panel(); infoPanel.BorderStyle = BorderStyle.FixedSingle; infoPanel.Location = new Point(24, 308); infoPanel.Size = new Size(424, 48); Controls.Add(infoPanel);
            lblOutput = NewLabel("预计输出：计算中...", 12, 7, 396, 22); lblOutput.Font = new Font(Font, FontStyle.Bold); infoPanel.Controls.Add(lblOutput);
            Label note = NewLabel("倍率越大越清晰，同时输出图片也会更大。", 12, 27, 396, 18); note.ForeColor = Color.DimGray; infoPanel.Controls.Add(note);

            btnOK = new Button(); btnOK.Text = "开始拼图"; btnOK.Location = new Point(272, 376); btnOK.Size = new Size(82, 32); Controls.Add(btnOK);
            Button btnCancel = new Button(); btnCancel.Text = "取消"; btnCancel.Location = new Point(366, 376); btnCancel.Size = new Size(74, 32); Controls.Add(btnCancel);
            AcceptButton = btnOK; CancelButton = btnCancel;

            btnOK.Click += BtnOK_Click;
            btnCancel.Click += delegate { DialogResult = DialogResult.Cancel; Close(); };
            numCols.ValueChanged += AnyChanged; radWidth.CheckedChanged += AnyChanged; radHeight.CheckedChanged += AnyChanged;
            numScale.ValueChanged += AnyChanged; numSpace.ValueChanged += AnyChanged; numMargin.ValueChanged += AnyChanged;

            Shown += SettingsForm_Shown;
            initialized = true;
            UpdatePreview();
        }

        private Label NewLabel(string text, int x, int y, int w, int h)
        {
            Label l = new Label(); l.Text = text; l.Location = new Point(x, y); l.Size = new Size(w, h); return l;
        }

        private void SettingsForm_Shown(object sender, EventArgs e)
        {
            TopMost = true;
            ForegroundHelper.Force(this);
            Activate();
            btnOK.Focus();
            System.Windows.Forms.Timer t = new System.Windows.Forms.Timer();
            t.Interval = 220;
            t.Tick += delegate(object s, EventArgs a)
            {
                t.Stop();
                TopMost = false;
                NativeMethods.SetWindowPos(Handle, NativeMethods.HWND_NOTOPMOST, 0, 0, 0, 0,
                    NativeMethods.SWP_NOMOVE | NativeMethods.SWP_NOSIZE | NativeMethods.SWP_SHOWWINDOW);
                Activate();
                t.Dispose();
            };
            t.Start();
        }

        private void AnyChanged(object sender, EventArgs e)
        {
            if (initialized) UpdatePreview();
        }

        private SettingsData ReadSettings()
        {
            SettingsData s = new SettingsData();
            s.Columns = (int)numCols.Value;
            s.Mode = radHeight.Checked ? AdaptMode.Height : AdaptMode.Width;
            s.Scale = (double)numScale.Value;
            s.Spacing = (int)numSpace.Value;
            s.Margin = (int)numMargin.Value;
            s.Format = Convert.ToString(cmbFormat.SelectedItem) == "PNG" ? "PNG" : "JPG";
            s.OpenAfter = chkOpen.Checked;
            return s;
        }

        private void UpdatePreview()
        {
            try
            {
                SettingsData s = ReadSettings();
                ImageInfo first = infos[0];
                int fw = (int)Math.Round(first.Width * s.Scale);
                int fh = (int)Math.Round(first.Height * s.Scale);
                lblExample.Text = "首张示例：" + first.Width + "×" + first.Height + " → 约 " + fw + "×" + fh + " px";
                LayoutData layout = GridLayoutEngine.GetLayout(infos, s);
                long pixels = (long)layout.Width * (long)layout.Height;
                if (GridLayoutEngine.IsTooLarge(layout))
                {
                    lblOutput.Text = "预计输出：" + layout.Width + " × " + layout.Height + " px  ·  尺寸过大";
                    lblOutput.ForeColor = Color.Firebrick; btnOK.Enabled = false;
                }
                else
                {
                    lblOutput.Text = "预计输出：" + layout.Width + " × " + layout.Height + " px  ·  约 " + (pixels / 1000000.0).ToString("0.0") + " MP";
                    lblOutput.ForeColor = Color.Black; btnOK.Enabled = true;
                }
            }
            catch
            {
                lblOutput.Text = "预计输出：无法计算"; lblOutput.ForeColor = Color.Firebrick; btnOK.Enabled = false;
            }
        }

        private void BtnOK_Click(object sender, EventArgs e)
        {
            SettingsData s = ReadSettings();
            LayoutData layout = GridLayoutEngine.GetLayout(infos, s);
            if (GridLayoutEngine.IsTooLarge(layout))
            {
                MessageBox.Show(this, "输出图片尺寸过大，请降低分辨率倍率或调整每排数量。", "拼图工具", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            ResultSettings = s;
            SettingsStore.Save(s);
            DialogResult = DialogResult.OK;
            Close();
        }
    }

    internal static class Composer
    {
        internal static string Compose(List<ImageInfo> infos, SettingsData s)
        {
            LayoutData layout = GridLayoutEngine.GetLayout(infos, s);
            if (GridLayoutEngine.IsTooLarge(layout))
                throw new InvalidOperationException("输出图片过大：" + layout.Width + " × " + layout.Height + "。请降低分辨率倍率或调整每排数量。");

            using (Bitmap bmp = new Bitmap(layout.Width, layout.Height, PixelFormat.Format24bppRgb))
            using (Graphics g = Graphics.FromImage(bmp))
            {
                g.CompositingMode = CompositingMode.SourceOver;
                g.CompositingQuality = CompositingQuality.HighQuality;
                g.InterpolationMode = InterpolationMode.HighQualityBicubic;
                g.SmoothingMode = SmoothingMode.HighQuality;
                g.PixelOffsetMode = PixelOffsetMode.HighQuality;
                g.Clear(Color.White);

                int gap = s.Spacing;
                int margin = s.Margin;
                if (layout.Mode == AdaptMode.Width)
                {
                    double y = margin;
                    for (int r = 0; r < layout.Rows.Count; r++)
                    {
                        RowLayout row = layout.Rows[r];
                        double x = margin + ((layout.Width - 2 * margin - row.Width) / 2.0);
                        for (int j = 0; j < row.Count; j++)
                        {
                            int idx = row.Start + j;
                            DrawImage(g, infos[idx].Path, new RectangleF((float)x, (float)y, (float)row.Widths[j], (float)row.Height));
                            x += row.Widths[j] + gap;
                        }
                        y += row.Height + gap;
                    }
                }
                else
                {
                    double y = margin;
                    for (int r = 0; r < layout.RowsCount; r++)
                    {
                        double x = margin;
                        double rh = layout.RowHeights[r];
                        for (int c = 0; c < layout.Columns; c++)
                        {
                            int idx = r * layout.Columns + c;
                            double cw = layout.ColWidths[c];
                            if (idx < infos.Count)
                            {
                                double ih = layout.ItemHeights[idx];
                                double iy = y + ((rh - ih) / 2.0);
                                DrawImage(g, infos[idx].Path, new RectangleF((float)x, (float)iy, (float)cw, (float)ih));
                            }
                            x += cw + gap;
                        }
                        y += rh + gap;
                    }
                }

                string output = GetOutputPath(infos[0].Path, s.Format);
                SaveBitmap(bmp, output, s.Format);
                return output;
            }
        }

        private static void DrawImage(Graphics g, string path, RectangleF rect)
        {
            using (Image img = Image.FromFile(path))
            {
                ImageUtil.ApplyOrientation(img);
                g.DrawImage(img, rect);
            }
        }

        private static string GetOutputPath(string firstImage, string format)
        {
            string dir = Path.GetDirectoryName(firstImage);
            string ext = format == "PNG" ? ".png" : ".jpg";
            string baseName = "拼图_" + DateTime.Now.ToString("yyyyMMdd_HHmmss");
            string candidate = Path.Combine(dir, baseName + ext);
            int i = 2;
            while (File.Exists(candidate))
            {
                candidate = Path.Combine(dir, baseName + "_" + i + ext); i++;
            }
            return candidate;
        }

        private static void SaveBitmap(Bitmap bitmap, string path, string format)
        {
            if (format == "PNG")
            {
                bitmap.Save(path, ImageFormat.Png); return;
            }
            ImageCodecInfo codec = null;
            ImageCodecInfo[] codecs = ImageCodecInfo.GetImageEncoders();
            for (int i = 0; i < codecs.Length; i++) if (codecs[i].MimeType == "image/jpeg") { codec = codecs[i]; break; }
            if (codec == null) { bitmap.Save(path, ImageFormat.Jpeg); return; }
            using (EncoderParameters ep = new EncoderParameters(1))
            {
                ep.Param[0] = new EncoderParameter(System.Drawing.Imaging.Encoder.Quality, 92L);
                bitmap.Save(path, codec, ep);
            }
        }
    }
}
