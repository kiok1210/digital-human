const $ = (selector) => document.querySelector(selector);
const form = $("#form");
const imageInput = $("#image");
const drop = $("#drop");
const preview = $("#preview");
const text = $("#text");
const submit = $("#submit");
const useBgm = $("#useBgm");
const bgmInput = $("#bgm");
const bgmField = $("#bgmField");

let imageData = "";
let bgmData = "";
let libraryBgm = "";
let libraryTracks = [];
let avatarChoice = "";
let sourceMode = "avatar";
let voices = [];
let fishVoices = [];
let creativeMode = "mixed";
let mediaAssets = {stock: [], stickers: []};
const selectedStock = new Set();
const selectedStickers = new Set();

/* ---------- 通用小工具 ---------- */

function show(name) {
  ["idle", "running", "done", "error"].forEach(id => $("#" + id).classList.toggle("hidden", id !== name));
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  })[char]);
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours) return `${hours} 小时 ${minutes} 分`;
  if (minutes) return `${minutes} 分 ${secs} 秒`;
  return `${secs} 秒`;
}

function formatClock(timestamp) {
  const date = new Date((Number(timestamp) || 0) * 1000);
  const pad = n => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function setView(name) {
  document.querySelectorAll(".tab").forEach(tab => tab.classList.toggle("on", tab.dataset.view === name));
  $("#view-studio").classList.toggle("hidden", name !== "studio");
  $("#view-history").classList.toggle("hidden", name !== "history");
  if (name === "history") loadHistory();
}

document.querySelectorAll(".tab").forEach(tab => tab.addEventListener("click", () => setView(tab.dataset.view)));

/* ---------- 人像来源 ---------- */

function setSource(mode) {
  sourceMode = mode;
  document.querySelectorAll(".src").forEach(btn => btn.classList.toggle("on", btn.dataset.src === mode));
  drop.classList.toggle("hidden", mode !== "upload");
  $("#avatarPane").classList.toggle("hidden", mode !== "avatar");
  preview.classList.toggle("hidden", mode !== "upload");
  drop.classList.toggle("has-image", mode === "upload" && !!imageData);
}

document.querySelectorAll(".src").forEach(btn =>
  btn.addEventListener("click", () => setSource(btn.dataset.src)));

function loadImage(file) {
  if (!file) return;
  if (!file.type.startsWith("image/")) return alert("请选择 JPG、PNG 或 WebP 图片。");
  if (file.size > 12 * 1024 * 1024) return alert("照片不能超过 12MB。");
  const reader = new FileReader();
  reader.onload = () => {
    imageData = reader.result;
    preview.src = imageData;
    setSource("upload");
    drop.classList.add("has-image");
  };
  reader.readAsDataURL(file);
}

async function loadAvatars() {
  const grid = $("#avatarGrid");
  try {
    const data = await fetch("/api/avatars").then(response => response.json());
    grid.innerHTML = "";
    if (!data.avatars || !data.avatars.length) {
      $("#avatarHint").textContent = "把预置人像放进 assets/avatars 后刷新，即可直接选用。";
      return;
    }
    $("#avatarHint").textContent = `共 ${data.avatars.length} 个预置形象，点击选择。`;
    data.avatars.forEach((item, index) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "avatar" + (index === 0 ? " on" : "");
      card.dataset.id = item.id;
      card.innerHTML = `<img src="${escapeHtml(item.url)}" alt="${escapeHtml(item.label)}"><span><b>${escapeHtml(item.label)}</b><small>AI 生成形象</small></span>`;
      if (index === 0) avatarChoice = item.id;
      card.addEventListener("click", () => {
        avatarChoice = item.id;
        imageData = "";
        grid.querySelectorAll(".avatar").forEach(node => node.classList.toggle("on", node.dataset.id === item.id));
        preview.src = item.url;
        drop.classList.add("has-image");
      });
      grid.append(card);
    });
  } catch {
    $("#avatarHint").textContent = "读取虚拟人像失败。";
  }
}

/* ---------- 配音音色 ---------- */

let providerState = null;

function fillEngineSelect(selectId, group) {
  const select = $("#" + selectId);
  select.innerHTML = "";
  (group.options || []).forEach(item => {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.ready ? item.label : `${item.label}（未配置）`;
    option.disabled = !item.ready;
    option.selected = item.id === group.current;
    select.append(option);
  });
}

function fillAcceleration(group) {
  const select = $("#acceleration");
  const previous = select.dataset.loaded ? select.value : group.current;
  select.innerHTML = "";
  (group.options || []).forEach(item => {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.label;
    option.disabled = !item.ready;
    option.selected = item.id === (previous || group.current);
    select.append(option);
  });
  if (!select.value) select.value = group.current || "auto";
  select.dataset.loaded = "1";

  const devices = (group.devices || []).map(item => {
    const memory = item.memory_mb ? ` ${Math.round(item.memory_mb / 1024 * 10) / 10}GB` : "";
    return `${item.name}${memory}`;
  }).join("、") || "未发现 GPU";
  const model = group.model || {};
  const video = group.video || {};
  $("#gpuHint").textContent = `${devices} · 口型 ${model.backend || "CPU"} · 编码 ${video.backend || "CPU"}；${video.reason || model.reason || ""}`;
}

function syncVoiceInputs() {
  const engine = $("#ttsEngine").value;
  const voiceEngine = engine === 'kokoro' ? 'kokoro' : 'piper';
  if ($("#voice").dataset.engine !== voiceEngine) {
    $("#voice").dataset.engine = voiceEngine;
    loadVoices();
  }
  const fish = engine === "fish" || engine === "fish_local";
  $("#voice").classList.toggle("hidden", fish);
  $("#fishVoice").classList.toggle("hidden", !fish);
  $("#fishStudio").classList.toggle("hidden", !fish);
  $("#fishTags").classList.toggle("hidden", !fish);
  if (fish) {
    const current = fishVoices.find(item => item.id === $("#fishVoice").value);
    $("#voiceTone").textContent = current
      ? `${current.description}（${current.id || "默认"}）`
      : engine === "fish_local" ? "本地 Fish Speech 音色" : "Fish Audio 音色";
  } else {
    updateVoiceTone();
  }
}

async function loadVoices() {
  const select = $("#voice");
  try {
    const engine = $("#ttsEngine").value === 'kokoro' ? 'kokoro' : 'piper';
    const data = await fetch(`/api/voices?engine=${engine}`).then(response => response.json());
    voices = data.voices || [];
    select.innerHTML = "";
    voices.forEach(item => {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.size_mb ? `${item.label} · ${item.size_mb}MB` : item.label;
      option.selected = item.id === data.current;
      select.append(option);
    });
    updateVoiceTone();
  } catch {
    select.innerHTML = "<option>音色读取失败</option>";
  }
}

function updateVoiceTone() {
  const current = voices.find(item => item.id === $("#voice").value);
  $("#voiceTone").textContent = current ? `${current.tone}（${current.id}）` : "—";
}

function renderFishVoices() {
  const select = $("#fishVoice");
  const selected = select.value;
  select.innerHTML = "";
  fishVoices.forEach(item => {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.label;
    select.append(option);
  });
  if (fishVoices.some(item => item.id === selected)) select.value = selected;

  const list = $("#fishVoiceList");
  list.innerHTML = "";
  fishVoices.forEach(item => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "fish-voice" + (item.id === select.value ? " on" : "");
    card.dataset.id = item.id;
    card.innerHTML = `<span>${escapeHtml(item.initial || "声")}</span><i><b>${escapeHtml(item.label)}</b><small>${escapeHtml(item.description || "本地音色")}</small></i>`;
    card.addEventListener("click", () => {
      select.value = item.id;
      list.querySelectorAll(".fish-voice").forEach(node => node.classList.toggle("on", node === card));
      syncVoiceInputs();
      if (item.audioUrl) {
        $("#fishPreviewAudio").src = item.audioUrl;
        $("#fishPreviewAudio").classList.remove("hidden");
        $("#fishPreviewHint").textContent = "正在播放参考录音";
        $("#fishPreviewAudio").play().catch(() => {});
      }
    });
    list.append(card);
  });
  syncVoiceInputs();
}

async function loadFishVoices(preferId = null) {
  try {
    const data = await fetch("/api/fish/voices").then(response => response.json());
    fishVoices = data.voices || [];
    renderFishVoices();
    if (preferId !== null && fishVoices.some(item => item.id === preferId)) {
      $("#fishVoice").value = preferId;
      renderFishVoices();
    }
  } catch {
    $("#fishVoiceList").innerHTML = '<p class="hint">音色库读取失败。</p>';
  }
}

document.querySelectorAll("[data-fish-tag]").forEach(button => {
  button.addEventListener("click", () => {
    const tag = button.dataset.fishTag;
    const start = text.selectionStart;
    const end = text.selectionEnd;
    text.setRangeText(tag, start, end, "end");
    text.focus();
    text.dispatchEvent(new Event("input"));
  });
});

$("#fishVoice").addEventListener("change", () => {
  renderFishVoices();
  syncVoiceInputs();
});

$("#fishPreview").addEventListener("click", async () => {
  const button = $("#fishPreview");
  const hint = $("#fishPreviewHint");
  if (!text.value.trim()) return alert("请先在上方输入一小段口播文案。");
  button.disabled = true;
  hint.textContent = "Fish Speech 正在生成，CPU 首次试听可能需要几分钟……";
  try {
    const response = await fetch("/api/fish/preview", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        text: text.value.trim().slice(0, 500),
        voice: $("#fishVoice").value,
        speed: Number($("#speed").value)
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "试听生成失败");
    const audio = $("#fishPreviewAudio");
    audio.src = data.url;
    audio.classList.remove("hidden");
    hint.textContent = "试听已生成";
    audio.play().catch(() => {});
  } catch (error) {
    hint.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

$("#fishVoiceSave").addEventListener("click", async () => {
  const file = $("#fishVoiceAudio").files[0];
  const label = $("#fishVoiceName").value.trim();
  const transcript = $("#fishVoiceTranscript").value.trim();
  const hint = $("#fishVoiceSaveHint");
  if (!label || !file || !transcript) return alert("请填写音色名称、选择录音，并逐字填写录音内容。");
  if (file.size > 20 * 1024 * 1024) return alert("参考录音不能超过 20MB。");
  const button = $("#fishVoiceSave");
  button.disabled = true;
  hint.textContent = "正在保存……";
  try {
    const audioData = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
    const response = await fetch("/api/fish/voices", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({label, transcript, audioData})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "保存音色失败");
    hint.textContent = "音色已保存并选中";
    await loadFishVoices(data.voice.id);
    $("#fishVoiceName").value = "";
    $("#fishVoiceAudio").value = "";
    $("#fishVoiceTranscript").value = "";
  } catch (error) {
    hint.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

$("#voice").addEventListener("change", updateVoiceTone);
["ttsEngine", "avatarEngine", "postEngine"].forEach(id => {
  // change handlers attached after health loads options
});


/* ---------- 背景音乐 ---------- */

function loadBgm(file) {
  if (!file) return;
  if (file.size > 18 * 1024 * 1024) return alert("音乐文件不能超过 18MB，建议先压缩。");
  const reader = new FileReader();
  reader.onload = () => {
    bgmData = reader.result;
    libraryBgm = "";
    $("#bgmName").textContent = file.name;
    renderBgmList();
  };
  reader.readAsDataURL(file);
}

function renderBgmList() {
  const list = $("#bgmList");
  list.innerHTML = "";

  libraryTracks.forEach(name => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip" + (libraryBgm === name ? " on" : "");
    chip.textContent = name;
    chip.addEventListener("click", () => {
      libraryBgm = libraryBgm === name ? "" : name;
      bgmData = "";
      $("#bgmName").textContent = libraryBgm || "选择本地音乐";
      renderBgmList();
    });
    list.append(chip);
  });

  if (bgmData) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip on";
    chip.textContent = "已上传本地音乐（点击清除）";
    chip.addEventListener("click", () => {
      bgmData = "";
      bgmInput.value = "";
      $("#bgmName").textContent = "选择本地音乐";
      renderBgmList();
    });
    list.append(chip);
  }
}

async function loadBgmLibrary() {
  try {
    const data = await fetch("/api/bgm").then(response => response.json());
    libraryTracks = data.tracks || [];
    if (!libraryTracks.length) {
      $("#bgmHint").textContent = "assets/bgm 目录为空，可直接上传本地音乐";
    } else {
      $("#bgmHint").textContent = `曲库 ${libraryTracks.length} 首，点击选用；也可上传本地音乐`;
      if (creativeMode === "mixed" && !bgmData && !libraryBgm) {
        libraryBgm = libraryTracks[0];
        useBgm.checked = true;
        bgmField.classList.remove("hidden");
        $("#bgmName").textContent = libraryBgm;
      }
    }
    renderBgmList();
  } catch {
    $("#bgmHint").textContent = "读取 assets/bgm 失败，可直接上传本地音乐";
  }
}

/* ---------- 素材混剪 ---------- */

function assetId(item) {
  return String(item.id || item.name || item.file || "");
}

function assetLabel(item) {
  return String(item.label || item.title || item.name || assetId(item));
}

function assetUrl(item) {
  return String(item.thumbnailUrl || item.thumbnail_url || item.url || "");
}

function updateMediaCounts() {
  $("#stockCount").textContent = `${selectedStock.size} 个已选`;
  $("#stickerCount").textContent = `${selectedStickers.size} 个已选`;
  const total = (mediaAssets.stock || []).length + (mediaAssets.stickers || []).length;
  $("#mediaHint").textContent = creativeMode === "portrait"
    ? "人像口播模式不会插入素材和贴纸"
    : `本地素材库 ${total} 项 · 点击卡片可增减`;
}

function renderMediaAssets() {
  const stockGrid = $("#stockGrid");
  const stickerGrid = $("#stickerGrid");
  stockGrid.innerHTML = "";
  stickerGrid.innerHTML = "";

  (mediaAssets.stock || []).forEach(item => {
    const id = assetId(item);
    if (!id) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "asset-card" + (selectedStock.has(id) ? " on" : "");
    button.title = assetLabel(item);
    const url = assetUrl(item);
    const isVideo = String(item.type || item.kind || "").toLowerCase() === "video" || /\.(mp4|mov|webm)$/i.test(url);
    button.innerHTML = `${isVideo
      ? `<video src="${escapeHtml(url)}" muted preload="metadata"></video>`
      : `<img src="${escapeHtml(url)}" loading="lazy" alt="">`}<span>${escapeHtml(assetLabel(item))}</span>`;
    button.addEventListener("click", () => {
      selectedStock.has(id) ? selectedStock.delete(id) : selectedStock.add(id);
      renderMediaAssets();
    });
    stockGrid.append(button);
  });

  (mediaAssets.stickers || []).forEach(item => {
    const id = assetId(item);
    if (!id) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "sticker-card" + (selectedStickers.has(id) ? " on" : "");
    button.title = assetLabel(item);
    button.innerHTML = `<img src="${escapeHtml(assetUrl(item))}" loading="lazy" alt="${escapeHtml(assetLabel(item))}">`;
    button.addEventListener("click", () => {
      selectedStickers.has(id) ? selectedStickers.delete(id) : selectedStickers.add(id);
      renderMediaAssets();
    });
    stickerGrid.append(button);
  });

  if (!stockGrid.children.length) stockGrid.innerHTML = '<p class="hint">暂无画面素材</p>';
  if (!stickerGrid.children.length) stickerGrid.innerHTML = '<p class="hint">暂无贴纸素材</p>';
  updateMediaCounts();
}

function autoPickMedia() {
  selectedStock.clear();
  selectedStickers.clear();
  const stock = mediaAssets.stock || [];
  const dance = stock.filter(item => /dance|woman|girl|ballet|舞/i.test(`${assetId(item)} ${assetLabel(item)}`));
  [...dance, ...stock.filter(item => !dance.includes(item))].slice(0, 4)
    .forEach(item => selectedStock.add(assetId(item)));
  (mediaAssets.stickers || []).slice(0, 3).forEach(item => selectedStickers.add(assetId(item)));
  renderMediaAssets();
}

function setCreativeMode(mode) {
  creativeMode = mode === "portrait" ? "portrait" : "mixed";
  document.querySelectorAll("[data-mix-mode]").forEach(button =>
    button.classList.toggle("on", button.dataset.mixMode === creativeMode));
  $(".media-step").classList.toggle("portrait-only", creativeMode === "portrait");
  if (creativeMode === "mixed") {
    if (!selectedStock.size && !selectedStickers.size) autoPickMedia();
    if (Number($("#segments").value) < 3) $("#segments").value = "3";
    if (libraryTracks.length && !bgmData && !libraryBgm) libraryBgm = libraryTracks[0];
    if (libraryBgm || bgmData) {
      useBgm.checked = true;
      bgmField.classList.remove("hidden");
      $("#bgmName").textContent = libraryBgm || "已上传本地音乐";
      renderBgmList();
    }
  }
  submit.childNodes[0].textContent = creativeMode === "mixed" ? "生成混剪口播 " : "生成人像口播 ";
  syncEditOptions();
  updateMediaCounts();
}

async function loadMediaAssets() {
  try {
    const data = await fetch("/api/media").then(response => response.json());
    mediaAssets = {
      stock: data.stock || data.assets || [],
      stickers: data.stickers || []
    };
    autoPickMedia();
  } catch {
    $("#mediaHint").textContent = "素材库读取失败，人像口播仍可使用";
    renderMediaAssets();
  }
}

document.querySelectorAll("[data-mix-mode]").forEach(button =>
  button.addEventListener("click", () => setCreativeMode(button.dataset.mixMode)));
$("#mediaAutoPick").addEventListener("click", autoPickMedia);

const mouthHints = {
  natural: "减少夸张张嘴，保持自然头部动作",
  clear: "增强口型幅度，适合快节奏讲解",
  expressive: "增加表情与姿态变化，适合活泼内容"
};
$("#mouthStyle").addEventListener("change", event => {
  $("#mouthHint").textContent = mouthHints[event.target.value] || mouthHints.natural;
});

/* ---------- 示例文案库 ---------- */

let presetIndustries = [];

async function loadPresets() {
  try {
    const data = await fetch("/api/presets").then(response => response.json());
    presetIndustries = data.industries || [];
    const total = presetIndustries.reduce((sum, group) => sum + group.items.length, 0);
    $("#presetTotal").textContent = total;

    const tabs = $("#industryTabs");
    tabs.innerHTML = "";
    presetIndustries.forEach((group, index) => {
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = "industry" + (index === 0 ? " on" : "");
      tab.dataset.id = group.id;
      tab.innerHTML = `<i>${escapeHtml(group.icon)}</i>${escapeHtml(group.industry)}`;
      tab.addEventListener("click", () => {
        tabs.querySelectorAll(".industry").forEach(node => node.classList.toggle("on", node === tab));
        renderPresets(group);
      });
      tabs.append(tab);
    });

    if (presetIndustries.length) renderPresets(presetIndustries[0]);
  } catch {
    $("#presetList").innerHTML = '<p class="hint">示例文案库读取失败。</p>';
  }
}

function renderPresets(group) {
  const list = $("#presetList");
  list.innerHTML = "";
  group.items.forEach(item => {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "preset";
    card.innerHTML = `
      <div class="preset-head"><b>${escapeHtml(item.title)}</b><span>${Number(item.length) || 0} 字</span></div>
      <p>${escapeHtml(item.text)}</p>
      <em>点击填入文案</em>`;
    card.addEventListener("click", () => {
      text.value = item.text;
      $("#count").textContent = text.value.length;
      list.querySelectorAll(".preset").forEach(node => node.classList.toggle("on", node === card));
      text.focus();
      text.scrollIntoView({behavior: "smooth", block: "center"});
    });
    list.append(card);
  });
}

/* ---------- 表单交互 ---------- */

imageInput.addEventListener("change", () => loadImage(imageInput.files[0]));
text.addEventListener("input", () => $("#count").textContent = text.value.length);
["dragenter", "dragover"].forEach(event => drop.addEventListener(event, e => { e.preventDefault(); drop.classList.add("drag"); }));
["dragleave", "drop"].forEach(event => drop.addEventListener(event, e => { e.preventDefault(); drop.classList.remove("drag"); }));
drop.addEventListener("drop", event => loadImage(event.dataTransfer.files[0]));

function syncEditOptions() {
  const multi = Number($("#segments").value) > 1;
  $("#introCard").disabled = !multi;
  $("#titleField").classList.toggle("hidden", !multi || !$("#introCard").checked);
  $("#transitionStyle").disabled = !multi;
}

useBgm.addEventListener("change", () => bgmField.classList.toggle("hidden", !useBgm.checked));
$("#introCard").addEventListener("change", syncEditOptions);
$("#segments").addEventListener("change", syncEditOptions);
bgmInput.addEventListener("change", () => loadBgm(bgmInput.files[0]));
$("#bgmVolume").addEventListener("input", event => $("#bgmVolumeText").textContent = event.target.value + "%");
$("#again").addEventListener("click", () => { show("idle"); loadHistory(); });
$("#retry").addEventListener("click", () => { show("idle"); submit.disabled = false; });
syncEditOptions();

/* ---------- 镜头时间一览 ---------- */

function renderTimeline(timeline, total) {
  const box = $("#timeline");
  box.innerHTML = "";
  if (!timeline || !timeline.length) {
    box.innerHTML = '<p class="hint">本次没有分镜信息。</p>';
    return;
  }
  const span = total || timeline[timeline.length - 1].end || 1;
  timeline.forEach(shot => {
    const row = document.createElement("div");
    row.className = "shot";
    const width = Math.max(4, (shot.duration / span) * 100);
    const left = Math.max(0, (shot.start / span) * 100);
    const label = escapeHtml(shot.name || `镜头 ${shot.index}`);
    row.innerHTML = `
      <div class="shot-head">
        <b>${label}</b>
        <span>${shot.duration}s</span>
      </div>
      <div class="shot-bar"><i style="left:${left}%;width:${width}%"></i></div>
      <div class="shot-meta">${shot.start}s → ${shot.end}s${shot.text ? ` · ${escapeHtml(shot.text)}` : ""}</div>`;
    box.append(row);
  });
}

/* ---------- 状态轮询 ---------- */

async function checkHealth() {
  try {
    const report = await fetch("/api/health").then(response => response.json());
    providerState = report.providers || null;
    if (providerState) {
      fillEngineSelect("ttsEngine", providerState.tts);
      fillEngineSelect("avatarEngine", providerState.avatar);
      fillEngineSelect("postEngine", providerState.post);
      if (providerState.fish_voice) $("#fishVoice").value = providerState.fish_voice;
      $("#ttsEngine").onchange = syncVoiceInputs;
      const localFish = providerState.tts.options.find(item => item.id === "fish_local");
      $("#fishRuntime").textContent = localFish && localFish.ready ? "本地服务已连接" : "本地服务正在准备";
      $("#fishRuntime").classList.toggle("ready", !!(localFish && localFish.ready));
      syncVoiceInputs();
    }
    if (report.acceleration) fillAcceleration(report.acceleration);
    $("#healthDot").className = report.ready ? "ok" : "bad";
    const engines = providerState
      ? `配音 ${providerState.tts.current} · 口型 ${providerState.avatar.current} · 后期 ${providerState.post.current}`
      : "";
    const acceleration = report.acceleration ? ` · ${report.acceleration.summary}` : "";
    $("#health").textContent = report.ready
      ? `本地模型已就绪 · ${report.voices ? report.voices.length : 0} 种音色 · ${engines}${acceleration}`
      : "尚未安装：" + report.missing.join("、");
    submit.disabled = !report.ready && !(providerState && providerState.avatar.options.some(item => item.id === "heygen" && item.ready));
  } catch {
    $("#health").textContent = "无法读取本地环境状态";
  }
}

async function poll(jobId) {
  let response;
  let job;
  try {
    response = await fetch(`/api/jobs/${jobId}`);
    job = await response.json();
    if (!response.ok) throw new Error(job.error || "任务状态已丢失，请重新生成。");
  } catch (pollError) {
    $("#errorText").textContent = pollError.message;
    show("error");
    submit.disabled = false;
    return;
  }
  $("#percent").textContent = `${job.progress || 0}%`;
  $("#bar").style.width = `${job.progress || 0}%`;
  $("#statusText").textContent = job.status === "running" ? "正在生成" : job.status;
  const elapsed = Math.max(0, Date.now() / 1000 - Number(job.created_at || Date.now() / 1000));
  const elapsedText = elapsed >= 60 ? ` · 已运行 ${Math.floor(elapsed / 60)} 分 ${Math.floor(elapsed % 60)} 秒` : ` · 已运行 ${Math.floor(elapsed)} 秒`;
  const cpuHint = (job.message || "").includes("口型") && (job.acceleration || "CPU").includes("CPU")
    ? " · CPU 口型每镜头约需 10–30 分钟"
    : "";
  $("#message").textContent = (job.message || "") + elapsedText + cpuHint;
  if (job.status === "complete") {
    $("#video").src = job.result;
    $("#download").href = job.result;
    $("#doneMeta").textContent = job.summary || "";
    $("#doneTiming").textContent = job.timing || "";
    $("#timelineTotal").textContent = job.duration ? `总时长 ${job.duration}s` : "";
    renderTimeline(job.timeline, job.duration);
    show("done");
    submit.disabled = false;
    loadHistory();
    return;
  }
  if (job.status === "failed") {
    $("#errorText").textContent = job.message;
    show("error");
    submit.disabled = false;
    loadHistory();
    return;
  }
  setTimeout(() => poll(jobId), 1800);
}

async function resumeActiveJob() {
  try {
    const data = await fetch("/api/jobs/active").then(response => response.json());
    if (!data.job || !data.job.id) return;
    submit.disabled = true;
    show("running");
    poll(data.job.id);
  } catch {
    // A missing active job is the normal idle state.
  }
}

form.addEventListener("submit", async event => {
  event.preventDefault();
  if (sourceMode === "upload" && !imageData) return alert("请先选择一张照片。");
  if (sourceMode === "avatar" && !avatarChoice) return alert("请从虚拟人像库中选择一个形象。");
  if (!text.value.trim()) return alert("请输入口播文案。");
  if (useBgm.checked && !bgmData && !libraryBgm) return alert("已勾选背景音乐，请选择上传音乐或选择曲库曲目。");

  submit.disabled = true;
  show("running");
  $("#doneMeta").textContent = "";
  $("#doneTiming").textContent = "";
  $("#timeline").innerHTML = "";
  $("#bar").style.width = "2%";
  $("#percent").textContent = "2%";
  $("#message").textContent = "任务已提交……";

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        imageData: sourceMode === "upload" ? imageData : "",
        avatar: sourceMode === "avatar" ? avatarChoice : "",
        text: text.value,
        ttsEngine: $("#ttsEngine").value,
        avatarEngine: $("#avatarEngine").value,
        postEngine: $("#postEngine").value,
        acceleration: $("#acceleration").value,
        voice: ["fish", "fish_local"].includes($("#ttsEngine").value) ? $("#fishVoice").value.trim() : $("#voice").value,
        portraitMode: $("#portraitMode").value,
        speed: Number($("#speed").value),
        captions: $("#captions").checked,
        segments: Number($("#segments").value),
        title: ($("#cardTitle").value.trim() || text.value.trim()).slice(0, 24),
        transitionStyle: $("#transitionStyle").value,
        introCard: Number($("#segments").value) > 1 && $("#introCard").checked,
        bgm: useBgm.checked ? (bgmData || libraryBgm) : "",
        bgmVolume: Number($("#bgmVolume").value) / 100,
        creativeMode,
        stockAssets: creativeMode === "mixed" ? [...selectedStock] : [],
        stickers: creativeMode === "mixed" ? [...selectedStickers] : [],
        mouthStyle: $("#mouthStyle").value,
        faceEnhance: $("#faceEnhance").checked
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error + (data.missing ? `\n缺少：${data.missing.join("、")}` : ""));
    poll(data.jobId);
  } catch (error) {
    $("#errorText").textContent = error.message;
    show("error");
    submit.disabled = false;
  }
});

/* ---------- 生成历史 ---------- */

async function loadHistory() {
  try {
    const data = await fetch("/api/history").then(response => response.json());
    const entries = data.entries || [];
    $("#historyCount").textContent = entries.length;
    const list = $("#historyList");
    list.innerHTML = "";

    if (!entries.length) {
      $("#historySummary").textContent = "还没有生成记录，去创作台试一条吧。";
      return;
    }
    const done = entries.filter(item => item.status === "complete");
    const totalSeconds = done.reduce((sum, item) => sum + (Number(item.duration) || 0), 0);
    const totalElapsed = done.reduce((sum, item) => sum + (Number(item.elapsed) || 0), 0);
    $("#historySummary").textContent =
      `共 ${entries.length} 条记录 · 成功 ${done.length} 条 · 成片累计 ${formatDuration(totalSeconds)} · 生成累计 ${formatDuration(totalElapsed)}`;

    entries.forEach(entry => {
      const card = document.createElement("article");
      card.className = "history-card" + (entry.status === "complete" ? "" : " bad");

      const ok = entry.status === "complete";
      const badge = ok ? '<span class="badge ok">已完成</span>' : '<span class="badge bad">失败</span>';
      const poster = entry.posterUrl ? `<img class="hposter" src="${entry.posterUrl}" alt="">` : '<div class="hposter empty"></div>';

      const shotRows = (entry.timeline || []).map(shot =>
        `<li><b>${escapeHtml(shot.name || "镜头" + shot.index)}</b><span>${shot.duration}s</span><em>${shot.start}s → ${shot.end}s</em></li>`
      ).join("");

      card.innerHTML = `
        ${poster}
        <div class="hbody">
          <div class="hhead">
            <h3>${escapeHtml(entry.title || "未命名成片")}</h3>
            ${badge}
          </div>
          <div class="hmeta">
            <span>${formatClock(entry.created_at)}</span>
            <span>成片 ${formatDuration(entry.duration)}</span>
            <span>耗时 ${formatDuration(entry.elapsed)}</span>
            <span>${entry.shots || 1} 镜头</span>
            <span>${escapeHtml(entry.voice || "")}</span>
          </div>
          ${shotRows ? `<ul class="hshots">${shotRows}</ul>` : ""}
          ${ok ? "" : `<p class="herr">${escapeHtml(entry.error || "生成失败")}</p>`}
          <div class="hactions">
            ${ok ? `<a class="mini" href="${entry.url}" target="_blank" rel="noopener">播放</a>
                    <a class="mini" href="${entry.url}" download>下载</a>` : ""}
            <button class="mini danger" type="button" data-del="${entry.id}">删除记录</button>
          </div>
        </div>`;
      list.append(card);
    });

    list.querySelectorAll("[data-del]").forEach(button => {
      button.addEventListener("click", async () => {
        await fetch(`/api/history/${button.dataset.del}`, {method: "DELETE"});
        loadHistory();
      });
    });
  } catch {
    $("#historySummary").textContent = "读取生成历史失败。";
  }
}

$("#clearHistory").addEventListener("click", async () => {
  if (!confirm("确定清空全部生成历史记录吗？成片文件不会被删除。")) return;
  await fetch("/api/history/all", {method: "DELETE"});
  loadHistory();
});

/* ---------- 启动 ---------- */

setSource("avatar");
checkHealth();
loadBgmLibrary();
loadMediaAssets();
loadVoices();
loadFishVoices();
loadAvatars();
loadPresets();
loadHistory();
resumeActiveJob();
