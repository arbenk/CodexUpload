chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id || !tab.url?.startsWith("https://g2.ltfc.net/view/")) {
    await chrome.tabs.create({ url: chrome.runtime.getURL("downloader.html?error=请先打开中华珍宝馆的大图查看页面") });
    return;
  }

  try {
    let data;
    try {
      data = await chrome.tabs.sendMessage(tab.id, { type: "extract-work" });
    } catch (_missingContentScript) {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
      data = await chrome.tabs.sendMessage(tab.id, { type: "extract-work" });
    }
    if (!data?.images?.length) throw new Error("页面中没有识别到大图数据，请等待页面加载完成后重试。");
    const jobId = crypto.randomUUID();
    await chrome.storage.session.set({ [`job:${jobId}`]: data });
    await chrome.tabs.create({ url: chrome.runtime.getURL(`downloader.html?job=${jobId}`) });
  } catch (error) {
    const message = encodeURIComponent(error?.message || "无法读取当前页面，请刷新后重试。");
    await chrome.tabs.create({ url: chrome.runtime.getURL(`downloader.html?error=${message}`) });
  }
});
