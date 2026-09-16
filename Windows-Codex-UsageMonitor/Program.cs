using System.Text;
using System.Text.Json;

namespace CodexUsageMini;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        using var mutex = new Mutex(true, "Local\\CodexUsageMini.SingleInstance", out bool first);
        if (!first) return;
        ApplicationConfiguration.Initialize();
        Application.Run(new UsageForm());
    }
}

internal sealed record WindowUsage(double UsedPercent, int WindowMinutes, long ResetsAt);
internal sealed record UsageSnapshot(WindowUsage? Primary, WindowUsage? Secondary, DateTime SeenAt);

internal sealed class UsageForm : Form
{
    private readonly string sessionsPath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".codex", "sessions");
    private readonly FileSystemWatcher? watcher;
    private readonly System.Windows.Forms.Timer debounce = new() { Interval = 180 };
    private readonly System.Windows.Forms.Timer clock = new() { Interval = 15_000 };
    private readonly ToolTip tips = new();
    private UsageSnapshot? snapshot;
    private bool dragging;
    private Point dragOrigin;
    private Point formOrigin;
    private bool pinned;

    public UsageForm()
    {
        Text = "Codex 用量";
        AutoScaleMode = AutoScaleMode.None;
        ClientSize = new Size(246, 94);
        FormBorderStyle = FormBorderStyle.None;
        StartPosition = FormStartPosition.Manual;
        ShowInTaskbar = true;
        BackColor = Color.FromArgb(24, 25, 28);
        ForeColor = Color.FromArgb(235, 235, 238);
        Font = new Font("Microsoft YaHei UI", 9f, FontStyle.Regular, GraphicsUnit.Point);
        DoubleBuffered = true;
        Location = new Point(Screen.PrimaryScreen!.WorkingArea.Right - Width - 12,
                             Screen.PrimaryScreen.WorkingArea.Bottom - Height - 12);

        MouseDown += BeginDrag;
        MouseMove += ContinueDrag;
        MouseUp += (_, _) => dragging = false;
        DoubleClick += (_, _) => RefreshUsage();
        Paint += PaintWindow;

        debounce.Tick += (_, _) => { debounce.Stop(); RefreshUsage(); };
        clock.Tick += (_, _) => Invalidate();
        clock.Start();

        if (Directory.Exists(sessionsPath))
        {
            watcher = new FileSystemWatcher(sessionsPath, "*.jsonl")
            {
                IncludeSubdirectories = true,
                NotifyFilter = NotifyFilters.LastWrite | NotifyFilters.FileName | NotifyFilters.CreationTime,
                EnableRaisingEvents = true
            };
            watcher.Changed += FileChanged;
            watcher.Created += FileChanged;
            watcher.Renamed += FileChanged;
        }

        RefreshUsage();
    }

    protected override void OnFormClosed(FormClosedEventArgs e)
    {
        watcher?.Dispose();
        debounce.Dispose();
        clock.Dispose();
        tips.Dispose();
        base.OnFormClosed(e);
    }

    private void FileChanged(object sender, FileSystemEventArgs e)
    {
        if (IsDisposed) return;
        BeginInvoke(() => { debounce.Stop(); debounce.Start(); });
    }

    private void RefreshUsage()
    {
        UsageSnapshot? latest = UsageReader.FindLatest(sessionsPath);
        if (latest is not null) snapshot = latest;
        Invalidate();
    }

    private void PaintWindow(object? sender, PaintEventArgs e)
    {
        var g = e.Graphics;
        g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
        using var border = new Pen(Color.FromArgb(62, 64, 70));
        g.DrawRectangle(border, 0, 0, ClientSize.Width - 1, ClientSize.Height - 1);

        using var titleFont = new Font(Font.FontFamily, 8.3f, FontStyle.Bold);
        using var smallFont = new Font(Font.FontFamily, 7.5f);
        using var valueFont = new Font("Segoe UI", 10.5f, FontStyle.Bold);
        using var titleBrush = new SolidBrush(Color.FromArgb(160, 162, 170));
        using var textBrush = new SolidBrush(ForeColor);
        using var dimBrush = new SolidBrush(Color.FromArgb(145, 147, 154));

        g.DrawString("CODEX  剩余", titleFont, titleBrush, 9, 6);
        DrawButton(g, new Rectangle(198, 3, 22, 20), pinned ? "●" : "○", pinned);
        DrawButton(g, new Rectangle(222, 3, 20, 20), "×", false);

        if (snapshot is null)
        {
            g.DrawString(Directory.Exists(sessionsPath) ? "等待用量数据…" : "未找到 Codex 日志", Font, dimBrush, 9, 40);
            return;
        }

        DrawUsage(g, snapshot.Primary, "5小时", 31, valueFont, smallFont, textBrush, dimBrush);
        DrawUsage(g, snapshot.Secondary, "7天", 62, valueFont, smallFont, textBrush, dimBrush);
    }

    private static void DrawButton(Graphics g, Rectangle r, string text, bool active)
    {
        if (active)
        {
            using var bg = new SolidBrush(Color.FromArgb(49, 103, 78));
            g.FillRectangle(bg, r);
        }
        using var b = new SolidBrush(active ? Color.White : Color.FromArgb(165, 167, 173));
        using var f = new Font("Segoe UI Symbol", 9f);
        var sf = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center };
        g.DrawString(text, f, b, r, sf);
    }

    private static void DrawUsage(Graphics g, WindowUsage? item, string fallbackName, int y,
        Font valueFont, Font smallFont, Brush textBrush, Brush dimBrush)
    {
        string name = item is null ? fallbackName : FormatWindow(item.WindowMinutes, fallbackName);
        g.DrawString(name, smallFont, dimBrush, 9, y + 3);
        if (item is null)
        {
            g.DrawString("--", valueFont, textBrush, 58, y);
            return;
        }
        double remaining = Math.Clamp(100d - item.UsedPercent, 0d, 100d);
        string value = $"{remaining:0.#}%";
        g.DrawString(value, valueFont, textBrush, 55, y - 1);
        g.DrawString("重置 " + FormatRemaining(item.ResetsAt), smallFont, dimBrush, 124, y + 3);

        var track = new Rectangle(9, y + 24, 228, 3);
        using var trackBrush = new SolidBrush(Color.FromArgb(50, 52, 58));
        Color c = remaining <= 15 ? Color.FromArgb(226, 83, 83) :
                  remaining <= 35 ? Color.FromArgb(229, 166, 64) : Color.FromArgb(74, 190, 128);
        using var fill = new SolidBrush(c);
        g.FillRectangle(trackBrush, track);
        g.FillRectangle(fill, new Rectangle(track.X, track.Y, (int)Math.Round(track.Width * remaining / 100d), track.Height));
    }

    private static string FormatWindow(int minutes, string fallback) => minutes switch
    {
        300 => "5小时",
        10080 => "7天",
        _ when minutes >= 1440 => $"{minutes / 1440d:0.#}天",
        _ when minutes >= 60 => $"{minutes / 60d:0.#}小时",
        _ => fallback
    };

    private static string FormatRemaining(long unixSeconds)
    {
        var span = DateTimeOffset.FromUnixTimeSeconds(unixSeconds) - DateTimeOffset.Now;
        if (span <= TimeSpan.Zero) return "即将更新";
        if (span.TotalDays >= 1) return $"{(int)span.TotalDays}天{span.Hours}时";
        if (span.TotalHours >= 1) return $"{(int)span.TotalHours}时{span.Minutes}分";
        return $"{Math.Max(1, span.Minutes)}分";
    }

    protected override void OnMouseClick(MouseEventArgs e)
    {
        if (e.Button == MouseButtons.Left && new Rectangle(198, 3, 22, 20).Contains(e.Location))
        {
            pinned = !pinned;
            TopMost = pinned;
            Invalidate();
        }
        else if (e.Button == MouseButtons.Left && new Rectangle(222, 3, 20, 20).Contains(e.Location)) Close();
        base.OnMouseClick(e);
    }

    private void BeginDrag(object? sender, MouseEventArgs e)
    {
        if (e.Button != MouseButtons.Left || e.Y > 27 || e.X >= 194) return;
        dragging = true;
        dragOrigin = Cursor.Position;
        formOrigin = Location;
    }

    private void ContinueDrag(object? sender, MouseEventArgs e)
    {
        if (!dragging) return;
        var delta = new Size(Cursor.Position.X - dragOrigin.X, Cursor.Position.Y - dragOrigin.Y);
        Location = formOrigin + delta;
    }
}

internal static class UsageReader
{
    private const int TailBytes = 1024 * 1024;

    public static UsageSnapshot? FindLatest(string sessionsPath)
    {
        if (!Directory.Exists(sessionsPath)) return null;
        try
        {
            foreach (var file in Directory.EnumerateFiles(sessionsPath, "*.jsonl", SearchOption.AllDirectories)
                         .Select(p => new FileInfo(p)).OrderByDescending(f => f.LastWriteTimeUtc).Take(24))
            {
                var result = ReadLatestFromFile(file.FullName);
                if (result is not null) return result;
            }
        }
        catch { }
        return null;
    }

    private static UsageSnapshot? ReadLatestFromFile(string path)
    {
        try
        {
            using var stream = new FileStream(path, FileMode.Open, FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete, 64 * 1024, FileOptions.SequentialScan);
            long start = Math.Max(0, stream.Length - TailBytes);
            stream.Position = start;
            using var reader = new StreamReader(stream, Encoding.UTF8, true, 64 * 1024);
            if (start > 0) reader.ReadLine();
            var lines = new List<string>();
            string? line;
            while ((line = reader.ReadLine()) is not null) lines.Add(line);

            for (int i = lines.Count - 1; i >= 0; i--)
            {
                if (!lines[i].Contains("\"rate_limits\"", StringComparison.Ordinal)) continue;
                try
                {
                    using var doc = JsonDocument.Parse(lines[i]);
                    var root = doc.RootElement;
                    if (!root.TryGetProperty("payload", out var payload) ||
                        !payload.TryGetProperty("rate_limits", out var limits)) continue;
                    WindowUsage? primary = ParseWindow(limits, "primary");
                    WindowUsage? secondary = ParseWindow(limits, "secondary");
                    if (primary is not null || secondary is not null)
                        return new UsageSnapshot(primary, secondary, File.GetLastWriteTime(path));
                }
                catch (JsonException) { }
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        return null;
    }

    private static WindowUsage? ParseWindow(JsonElement limits, string name)
    {
        if (!limits.TryGetProperty(name, out var item) || item.ValueKind != JsonValueKind.Object) return null;
        if (!item.TryGetProperty("used_percent", out var used) || !used.TryGetDouble(out double usedValue)) return null;
        int minutes = item.TryGetProperty("window_minutes", out var window) && window.TryGetInt32(out int m) ? m : 0;
        long reset = item.TryGetProperty("resets_at", out var at) && at.TryGetInt64(out long r) ? r : 0;
        return new WindowUsage(usedValue, minutes, reset);
    }
}
