const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
let job = null;
let cancelled = false;

function log(message) {
  const stamp = new Date().toLocaleTimeString();
  $("log").textContent += `[${stamp}] ${message}\n`;
  $("log").scrollTop = $("log").scrollHeight;
}

function safeName(value) {
  return value.replace(/[\\/:*?"<>|]/g, "_").replace(/\s+/g, " ").trim();
}

function setProgress(done, total, label) {
  const percent = total ? Math.round(done / total * 100) : 0;
  $("progress").style.width = `${percent}%`;
  $("percent").textContent = `${percent}%`;
  $("status").textContent = label;
}

function render(data) {
  job = data;
  $("subtitle").textContent = data.title;
  $("countText").textContent = `${data.images.length} 张独立大图`;
  const pixels = data.images.reduce((sum, image) => sum + image.width * image.height, 0);
  $("sizeText").textContent = `总像素约 ${(pixels / 1e6).toFixed(1)} MP`;
  $("imageList").innerHTML = data.images.map(image => `
    <label class="image-card">
      <input type="checkbox" data-index="${image.index}" checked>
      <span>
        <span class="image-title">第 ${String(image.index).padStart(2, "0")} 张</span>
        <span class="image-meta">${image.width} × ${image.height} · 层级 ${image.maxLevel}</span>
      </span>
    </label>`).join("");
  $("workspace").classList.remove("hidden");
}

function signedTileUrl(image, x, y) {
  const path = `/cagstore/${image.resourceId}/${image.maxLevel}/${x}_${y}.jpg`;
  const timestamp = Math.floor(Date.now() / 1000);
  const signature = md5(`${path}-${timestamp}-0-0-ltfcdotnet`);
  return `https://cag-ac.ltfc.net${path}?auth_key=${timestamp}-0-0-${signature}`;
}

async function fetchTile(image, x, y) {
  let error;
  for (let attempt = 1; attempt <= 3; attempt++) {
    if (cancelled) throw new Error("任务已取消");
    try {
      const response = await fetch(signedTileUrl(image, x, y), { referrer: "https://g2.ltfc.net/" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await createImageBitmap(await response.blob());
    } catch (caught) {
      error = caught;
      if (attempt < 3) await new Promise(resolve => setTimeout(resolve, 350 * attempt));
    }
  }
  throw new Error(`切片 ${x}_${y} 下载失败：${error?.message || error}`);
}

async function mapLimit(items, limit, worker) {
  let cursor = 0;
  const runners = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor++;
      await worker(items[index], index);
    }
  });
  await Promise.all(runners);
}

async function downloadImage(image, sequence, totalImages, completedTiles, totalTiles) {
  if (image.width > 32767 || image.height > 32767 || image.width * image.height > 130_000_000) {
    throw new Error(`第 ${image.index} 张尺寸过大，超出浏览器安全拼接限制。`);
  }
  const canvas = document.createElement("canvas");
  canvas.width = image.width;
  canvas.height = image.height;
  const context = canvas.getContext("2d", { alpha: false });
  const cols = Math.ceil(image.width / 512);
  const rows = Math.ceil(image.height / 512);
  const tiles = [];
  for (let y = 0; y < rows; y++) for (let x = 0; x < cols; x++) tiles.push({ x, y });
  const concurrency = Number($("concurrency").value);

  log(`开始第 ${String(image.index).padStart(2, "0")} 张：${image.width} × ${image.height}，${tiles.length} 个切片`);
  await mapLimit(tiles, concurrency, async ({ x, y }) => {
    const bitmap = await fetchTile(image, x, y);
    context.drawImage(bitmap, x * 512, y * 512);
    bitmap.close();
    completedTiles.value++;
    setProgress(completedTiles.value, totalTiles, `正在处理第 ${sequence}/${totalImages} 张`);
  });

  if (cancelled) throw new Error("任务已取消");
  const format = $("format").value;
  const mime = format === "jpeg" ? "image/jpeg" : "image/png";
  const ext = format === "jpeg" ? "jpg" : "png";
  const quality = format === "jpeg" ? 0.96 : undefined;
  const blob = await new Promise((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error("图片编码失败")), mime, quality));
  const url = URL.createObjectURL(blob);
  const filename = `${safeName(job.title)}/${safeName(job.title)}_${String(image.index).padStart(2, "0")}_100%_${image.width}x${image.height}.${ext}`;
  await chrome.downloads.download({ url, filename, saveAs: false, conflictAction: "uniquify" });
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
  log(`已提交下载：${filename}`);
}

async function startDownload() {
  const selected = [...document.querySelectorAll("input[data-index]:checked")]
    .map(input => job.images.find(image => image.index === Number(input.dataset.index)))
    .filter(Boolean);
  if (!selected.length) return alert("请至少选择一张图片。");

  cancelled = false;
  $("start").disabled = true;
  $("selectAll").disabled = true;
  $("cancel").classList.remove("hidden");
  $("log").textContent = "";
  const tileCount = image => Math.ceil(image.width / 512) * Math.ceil(image.height / 512);
  const totalTiles = selected.reduce((sum, image) => sum + tileCount(image), 0);
  const completedTiles = { value: 0 };

  try {
    for (let i = 0; i < selected.length; i++) {
      await downloadImage(selected[i], i + 1, selected.length, completedTiles, totalTiles);
    }
    setProgress(totalTiles, totalTiles, `完成：${selected.length} 张原图`);
    log("全部任务完成。请在 Edge 下载列表中查看文件。");
  } catch (error) {
    $("status").textContent = cancelled ? "任务已取消" : "任务失败";
    log(error?.message || String(error));
  } finally {
    $("start").disabled = false;
    $("selectAll").disabled = false;
    $("cancel").classList.add("hidden");
  }
}

$("selectAll").addEventListener("click", () => {
  const boxes = [...document.querySelectorAll("input[data-index]")];
  const shouldCheck = boxes.some(box => !box.checked);
  boxes.forEach(box => { box.checked = shouldCheck; });
});
$("start").addEventListener("click", startDownload);
$("cancel").addEventListener("click", () => { cancelled = true; log("正在停止任务…"); });

(async () => {
  const error = params.get("error");
  if (error) {
    $("errorPanel").textContent = error;
    $("errorPanel").classList.remove("hidden");
    $("subtitle").textContent = "无法启动下载器";
    return;
  }
  const jobId = params.get("job");
  const key = `job:${jobId}`;
  const stored = await chrome.storage.session.get(key);
  if (!stored[key]) {
    $("errorPanel").textContent = "下载任务数据已失效，请回到作品页面重新点击插件图标。";
    $("errorPanel").classList.remove("hidden");
    return;
  }
  render(stored[key]);
  await chrome.storage.session.remove(key);
})();
