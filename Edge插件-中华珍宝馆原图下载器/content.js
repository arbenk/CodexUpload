function cleanTitle(title) {
  return title
    .replace(/\s*[-·]\s*中华珍宝馆.*$/u, "")
    .replace(/\s*-\s*[^-]*高清大图在线欣赏.*$/u, "")
    .trim() || "中华珍宝馆原图";
}

function extractImages() {
  const source = [...document.scripts].map((script) => script.textContent || "").join("\n");
  const patterns = [
    /\\?"resourceId\\?":\\?"([0-9a-f]{24})\\?".*?\\?"maxlevel\\?":(\d+).*?\\?"minlevel\\?":(\d+).*?\\?"size\\?":\{\\?"width\\?":(\d+),\\?"height\\?":(\d+)\}/g,
    /"resourceId":"([0-9a-f]{24})".*?"maxlevel":(\d+).*?"minlevel":(\d+).*?"size":\{"width":(\d+),"height":(\d+)\}/g
  ];

  const found = [];
  const seen = new Set();
  for (const pattern of patterns) {
    for (const match of source.matchAll(pattern)) {
      if (seen.has(match[1])) continue;
      seen.add(match[1]);
      found.push({
        resourceId: match[1],
        maxLevel: Number(match[2]),
        minLevel: Number(match[3]),
        width: Number(match[4]),
        height: Number(match[5])
      });
    }
  }

  return {
    title: cleanTitle(document.title),
    pageUrl: location.href,
    images: found.map((image, index) => ({ ...image, index: index + 1 }))
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "extract-work") return;
  sendResponse(extractImages());
});
