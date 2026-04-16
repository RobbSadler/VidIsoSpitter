/* =========================================================
   Hi-8 Digitizer — app.js
   ========================================================= */

// ── State ────────────────────────────────────────────────
let currentProject = null;   // sanitized project name used in API paths
let activeVideo    = null;   // { card, video, overlay } currently playing

// ── DOM refs ─────────────────────────────────────────────
const errorBanner   = document.getElementById('error-banner');
const setupArea     = document.getElementById('setup-area');
const setupPanel    = document.getElementById('setup-panel');
const inputName     = document.getElementById('input-name');
const inputVideo    = document.getElementById('input-video');
const browseBtn     = document.getElementById('browse-btn');
const previewWrap   = document.getElementById('preview-wrap');
const previewVideo  = document.getElementById('preview-video');
const loadBtn       = document.getElementById('load-btn');
const recentSearch  = document.getElementById('recent-search');
const recentList    = document.getElementById('recent-list');
const settingsBtn       = document.getElementById('settings-btn');
const settingsOverlay   = document.getElementById('settings-overlay');
const settingsDir       = document.getElementById('settings-dir');
const settingsBrowseBtn = document.getElementById('settings-browse-btn');
const settingsCancelBtn = document.getElementById('settings-cancel-btn');
const settingsSaveBtn   = document.getElementById('settings-save-btn');
const firstrunOverlay   = document.getElementById('firstrun-overlay');
const firstrunDir       = document.getElementById('firstrun-dir');
const firstrunBrowse    = document.getElementById('firstrun-browse-btn');
const firstrunSave      = document.getElementById('firstrun-save-btn');
const projectHeader = document.getElementById('project-header');
const clipCount     = document.getElementById('clip-count');
const splitBtn        = document.getElementById('split-btn');
const closeBtn        = document.getElementById('close-btn');
const projectTitleEl  = document.getElementById('project-title');
const projectTitleInput = document.getElementById('project-title-input');
const projectDivider  = document.getElementById('project-divider');
const logPanel      = document.getElementById('log-panel');
const logOutput     = document.getElementById('log-output');
const clipGrid      = document.getElementById('clip-grid');
const publishBtn    = document.getElementById('publish-btn');

// ── Error display ─────────────────────────────────────────
function showError(msg) {
  errorBanner.textContent = msg;
  errorBanner.style.display = 'block';
}
function clearError() {
  errorBanner.style.display = 'none';
  errorBanner.textContent = '';
}

// ── Log panel ─────────────────────────────────────────────
function showLog() {
  logPanel.style.display = 'block';
  logPanel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}
function hideLog() {
  logPanel.style.display = 'none';
  logOutput.textContent = '';
}
function appendLog(line) {
  logOutput.textContent += line + '\n';
  logPanel.scrollTop = logPanel.scrollHeight;
}

let elapsedTimer = null;
function startElapsed() {
  const start = Date.now();
  function fmt(ms) {
    const s = Math.floor(ms / 1000);
    const m = Math.floor(s / 60);
    return m > 0 ? `${m}m ${s % 60}s` : `${s}s`;
  }
  // Reserve the last line for the timer
  logOutput.textContent += '⏱ Elapsed: 0s\n';
  elapsedTimer = setInterval(() => {
    const lines = logOutput.textContent.split('\n');
    lines[lines.length - 2] = `⏱ Elapsed: ${fmt(Date.now() - start)}`;
    logOutput.textContent = lines.join('\n');
    logPanel.scrollTop = logPanel.scrollHeight;
  }, 1000);
}
function stopElapsed() {
  clearInterval(elapsedTimer);
  elapsedTimer = null;
  // Remove the timer line
  const lines = logOutput.textContent.split('\n').filter(l => !l.startsWith('⏱'));
  logOutput.textContent = lines.join('\n');
}

// ── API wrapper functions ─────────────────────────────────

async function apiCall(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

async function createProject(name, videoPath) {
  return apiCall('POST', '/api/project/create', {
    project_name: name,
    source_video: videoPath,
  });
}

async function getClips(projectName) {
  return apiCall('GET', `/api/project/${projectName}/clips`);
}

async function splitVideo(projectName) {
  return apiCall('POST', `/api/project/${projectName}/split`);
}

async function updateTitle(projectName, clipId, title) {
  return apiCall('PATCH', `/api/project/${projectName}/clips/${clipId}`, { title });
}

async function deleteClip(projectName, clipId) {
  return apiCall('DELETE', `/api/project/${projectName}/clips/${clipId}`);
}

async function mergeClipNext(projectName, clipId) {
  return apiCall('POST', `/api/project/${projectName}/clips/${clipId}/merge_next`);
}

async function publishDVD(projectName) {
  return apiCall('POST', `/api/project/${projectName}/publish`);
}

// ── Project header ────────────────────────────────────────
function showProjectHeader(name, count) {
  projectTitleEl.textContent = `Project: ${name}`;
  clipCount.textContent = `${count} clip${count !== 1 ? 's' : ''}`;
  projectHeader.style.display = 'flex';
  projectDivider.style.display = 'block';
  publishBtn.style.display = 'block';
}

function updateClipCount(count) {
  clipCount.textContent = `${count} clip${count !== 1 ? 's' : ''}`;
}

// ── Hide setup area (always stop the preview first) ──────
function hideSetupArea() {
  previewVideo.pause();
  previewVideo.src = '';
  previewWrap.style.display = 'none';
  setupArea.style.display = 'none';
}

// ── Close project ─────────────────────────────────────────
closeBtn.addEventListener('click', () => {
  currentProject = null;
  activeVideo = null;
  clipGrid.innerHTML = '';
  projectHeader.style.display = 'none';
  projectDivider.style.display = 'none';
  publishBtn.style.display = 'none';
  hideLog();
  clearError();
  setupArea.style.display = 'flex';
  loadRecentProjects();
});

// ── Rename project (click title to edit) ─────────────────
projectTitleEl.addEventListener('click', () => {
  const current = projectTitleEl.textContent.replace(/^Project:\s*/, '');
  projectTitleInput.value = current;
  projectTitleEl.style.display = 'none';
  projectTitleInput.style.display = 'inline-block';
  projectTitleInput.focus();
  projectTitleInput.select();
});

async function commitRename() {
  const newName = projectTitleInput.value.trim();
  projectTitleInput.style.display = 'none';
  projectTitleEl.style.display = '';
  const oldName = projectTitleEl.textContent.replace(/^Project:\s*/, '');
  if (!newName || newName === oldName) return;
  try {
    const result = await apiCall('PATCH', `/api/project/${currentProject}/rename`, { project_name: newName });
    currentProject = result.sanitized_name;
    projectTitleEl.textContent = `Project: ${result.project_name}`;
    loadRecentProjects();
  } catch (err) {
    showError(`Rename failed: ${err.message}`);
  }
}

projectTitleInput.addEventListener('blur', commitRename);
projectTitleInput.addEventListener('keydown', e => {
  if (e.key === 'Enter') projectTitleInput.blur();
  if (e.key === 'Escape') {
    projectTitleInput.value = '';
    projectTitleInput.blur();
  }
});

// ── Dot animation (split progress feedback) ──────────────
let dotTimer = null;
function startDots(btn, base) {
  let n = 0;
  dotTimer = setInterval(() => { n = (n + 1) % 4; btn.textContent = base + '.'.repeat(n || 3); }, 600);
}
function stopDots(btn, text) {
  clearInterval(dotTimer);
  dotTimer = null;
  btn.textContent = text;
}

// ── Publish button state ──────────────────────────────────
function refreshPublishBtn() {
  const inputs = clipGrid.querySelectorAll('.clip-meta input[type="text"]');
  const anyTitled = [...inputs].some(i => i.value.trim() !== '');
  publishBtn.disabled = !anyTitled;
}

// ── Stop any currently playing video ─────────────────────
function stopActiveVideo() {
  if (!activeVideo) return;
  const { card, video, overlay } = activeVideo;
  video.pause();
  video.src = '';
  video.style.display = 'none';
  card.querySelector('img').style.display = 'block';
  overlay.textContent = '▶';
  activeVideo = null;
}

// ── Build a clip card ─────────────────────────────────────
function buildCard(clip, index) {
  const card = document.createElement('div');
  card.className = 'clip-card';
  card.dataset.clipId = clip.id;

  // media wrapper
  const wrap = document.createElement('div');
  wrap.className = 'media-wrap';

  const img = document.createElement('img');
  img.src = clip.thumbnail_url;
  img.alt = clip.title || `Clip ${index + 1}`;
  img.loading = 'lazy';

  const video = document.createElement('video');
  video.preload = 'none';
  video.controls = true;

  const overlay = document.createElement('div');
  overlay.className = 'play-overlay';
  overlay.textContent = '▶';

  wrap.append(img, video, overlay);

  wrap.addEventListener('click', () => {
    if (activeVideo && activeVideo.card === card) {
      // toggle off
      stopActiveVideo();
      return;
    }
    stopActiveVideo();
    img.style.display = 'none';
    video.style.display = 'block';
    video.src = clip.stream_url;
    video.play();
    overlay.textContent = '■';
    activeVideo = { card, video, overlay };
  });

  video.addEventListener('ended', () => {
    stopActiveVideo();
  });

  // title row
  const meta = document.createElement('div');
  meta.className = 'clip-meta';

  const mergeBtn = document.createElement('button');
  mergeBtn.className = 'merge-btn';
  mergeBtn.title = 'Merge with next clip';
  mergeBtn.textContent = '⇒';

  mergeBtn.addEventListener('click', async () => {
    if (!confirm('Merge this clip with the next one? This cannot be undone.')) return;
    mergeBtn.disabled = true;
    stopActiveVideo();
    try {
      const updated = await mergeClipNext(currentProject, clip.id);
      // Reload the full clip list so order and thumbnails are fresh
      const data = await getClips(currentProject);
      renderClips(data.clips);
    } catch (err) {
      showError(`Merge failed: ${err.message}`);
      mergeBtn.disabled = false;
    }
  });

  const delBtn = document.createElement('button');
  delBtn.className = 'delete-btn';
  delBtn.title = 'Delete clip';
  delBtn.textContent = '🗑';

  const titleInput = document.createElement('input');
  titleInput.type = 'text';
  titleInput.value = clip.title || '';
  titleInput.placeholder = `Clip ${index + 1}`;

  const savedLabel = document.createElement('span');
  savedLabel.className = 'saved-label';
  savedLabel.textContent = '✓ Saved';

  function saveTitle() {
    const val = titleInput.value.trim();
    if (val === (clip.title || '')) return;
    updateTitle(currentProject, clip.id, val)
      .then(() => {
        clip.title = val;
        savedLabel.classList.add('show');
        setTimeout(() => savedLabel.classList.remove('show'), 1500);
        refreshPublishBtn();
      })
      .catch(err => showError(`Could not save title: ${err.message}`));
  }

  titleInput.addEventListener('blur', saveTitle);
  titleInput.addEventListener('keydown', e => { if (e.key === 'Enter') { titleInput.blur(); } });

  delBtn.addEventListener('click', () => {
    if (!confirm('Delete this clip? It can be recovered manually from the deleted/ folder.')) return;
    deleteClip(currentProject, clip.id)
      .then(() => {
        card.classList.add('fading');
        setTimeout(() => {
          card.remove();
          const remaining = clipGrid.querySelectorAll('.clip-card').length;
          updateClipCount(remaining);
          refreshPublishBtn();
        }, 400);
      })
      .catch(err => showError(`Could not delete clip: ${err.message}`));
  });

  meta.append(mergeBtn, delBtn, titleInput, savedLabel);
  card.append(wrap, meta);
  return card;
}

// ── Render clips ──────────────────────────────────────────
function renderClips(clips) {
  clipGrid.innerHTML = '';
  clips.forEach((clip, i) => clipGrid.appendChild(buildCard(clip, i)));
  // Disable merge button on the last card — nothing to merge into
  const cards = clipGrid.querySelectorAll('.clip-card');
  if (cards.length > 0) {
    const lastMerge = cards[cards.length - 1].querySelector('.merge-btn');
    if (lastMerge) lastMerge.disabled = true;
  }
  updateClipCount(clips.length);
  refreshPublishBtn();
  // Lock split button once clips exist — re-splitting would overwrite everything
  if (clips.length > 0) {
    splitBtn.disabled = true;
    splitBtn.textContent = '✓ Already Split';
    splitBtn.title = 'Delete all clips first to re-split';
  } else {
    splitBtn.disabled = false;
    splitBtn.textContent = '▶ Split Video';
    splitBtn.title = '';
  }
}

// ── Source video preview ──────────────────────────────────
function suggestProjectName(filePath) {
  // Extract filename without extension, then humanise it
  const base = filePath.replace(/\\/g, '/').split('/').pop().replace(/\.[^.]+$/, '');
  return base.replace(/[_\-]+/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function showSourcePreview(filePath) {
  const encoded = encodeURIComponent(filePath);
  previewVideo.src = `/api/preview_source?path=${encoded}`;
  previewWrap.style.display = 'block';
}

// ── Settings ──────────────────────────────────────────────
async function browseDir(targetInput) {
  const prev = targetInput.value;
  targetInput.value = 'Opening…';
  try {
    const data = await apiCall('GET', '/api/browse_dir');
    targetInput.value = data.path || prev;
  } catch (err) {
    targetInput.value = prev;
    showError(`Browse failed: ${err.message}`);
  }
}

async function saveProjectsDir(dirInput, onSuccess) {
  const dir = dirInput.value.trim();
  if (!dir) { showError('Please enter or browse to a folder.'); return; }
  try {
    await apiCall('POST', '/api/settings', { projects_dir: dir });
    onSuccess();
  } catch (err) {
    showError(`Could not save settings: ${err.message}`);
  }
}

// First-run overlay
firstrunBrowse.addEventListener('click', () => browseDir(firstrunDir));
firstrunSave.addEventListener('click', () =>
  saveProjectsDir(firstrunDir, () => {
    firstrunOverlay.classList.remove('open');
    loadRecentProjects();
  })
);

// Settings modal
settingsBtn.addEventListener('click', async () => {
  try {
    const s = await apiCall('GET', '/api/settings');
    settingsDir.value = s.projects_dir || '';
  } catch (_) {}
  settingsOverlay.classList.add('open');
});
settingsCancelBtn.addEventListener('click', () => settingsOverlay.classList.remove('open'));
settingsBrowseBtn.addEventListener('click', () => browseDir(settingsDir));
settingsSaveBtn.addEventListener('click', () =>
  saveProjectsDir(settingsDir, () => {
    settingsOverlay.classList.remove('open');
    loadRecentProjects();
  })
);

async function checkSettings() {
  try {
    const s = await apiCall('GET', '/api/settings');
    if (s.is_default) {
      // No directory has been explicitly chosen yet — prompt on first run
      firstrunOverlay.classList.add('open');
    }
  } catch (_) {}
}

// ── Recent projects ───────────────────────────────────────
let allProjects = [];

function escHtml(str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function renderRecentList(projects) {
  recentList.innerHTML = '';
  const shown = projects.slice(0, 10);
  if (shown.length === 0) {
    recentList.innerHTML = '<div class="recent-empty">No projects found</div>';
    return;
  }
  shown.forEach(p => {
    const item = document.createElement('div');
    item.className = 'recent-item';
    const date = (p.created_at || '').slice(0, 10);
    const count = p.clip_count ?? 0;
    const status = p.status || 'created';

    const body = document.createElement('div');
    body.className = 'recent-item-body';
    body.innerHTML =
      `<div class="recent-name">${escHtml(p.project_name)}</div>` +
      `<div class="recent-meta">` +
        `<span class="recent-status status-${escHtml(status)}">${escHtml(status.replace('_', ' '))}</span>` +
        `<span>${count} clip${count !== 1 ? 's' : ''}</span>` +
        `<span>${date}</span>` +
      `</div>`;
    body.addEventListener('click', () => openRecentProject(p));

    const delBtn = document.createElement('button');
    delBtn.className = 'recent-del';
    delBtn.title = 'Delete project';
    delBtn.textContent = '🗑';
    delBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm(`Permanently delete "${p.project_name}" and all its files? This cannot be undone.`)) return;
      try {
        await apiCall('DELETE', `/api/project/${p.sanitized_name}`);
        allProjects = allProjects.filter(x => x.sanitized_name !== p.sanitized_name);
        const q = recentSearch.value.trim().toLowerCase();
        renderRecentList(q ? allProjects.filter(x => x.project_name.toLowerCase().includes(q)) : allProjects);
      } catch (err) {
        showError(`Delete failed: ${err.message}`);
      }
    });

    item.append(body, delBtn);
    recentList.appendChild(item);
  });
}

async function openRecentProject(p) {
  clearError();
  currentProject = p.sanitized_name;
  try {
    const data = await getClips(p.sanitized_name);
    hideSetupArea();
    showProjectHeader(p.project_name, data.clips.length);
    renderClips(data.clips);
  } catch (err) {
    showError(`Failed to open project: ${err.message}`);
  }
}

async function loadRecentProjects() {
  try {
    allProjects = await apiCall('GET', '/api/projects');
    renderRecentList(allProjects);
  } catch (_) {
    recentList.innerHTML = '<div class="recent-empty">Could not load projects</div>';
  }
}

recentSearch.addEventListener('input', () => {
  const q = recentSearch.value.trim().toLowerCase();
  renderRecentList(q ? allProjects.filter(p => p.project_name.toLowerCase().includes(q)) : allProjects);
});

browseBtn.addEventListener('click', async () => {
  browseBtn.disabled = true;
  browseBtn.textContent = '…';
  try {
    const data = await apiCall('GET', '/api/browse');
    if (data.path) {
      inputVideo.value = data.path;
      if (!inputName.value.trim()) {
        inputName.value = suggestProjectName(data.path);
      }
      showSourcePreview(data.path);
    }
  } catch (err) {
    showError(`Browse failed: ${err.message}`);
  } finally {
    browseBtn.disabled = false;
    browseBtn.textContent = 'Browse…';
  }
});

// ── Load / Create project ─────────────────────────────────
loadBtn.addEventListener('click', async () => {
  clearError();
  const name  = inputName.value.trim();
  const video = inputVideo.value.trim();
  if (!name || !video) { showError('Both Project Name and Source Video are required.'); return; }

  loadBtn.disabled = true;
  loadBtn.textContent = 'Loading…';

  try {
    let project;
    try {
      project = await createProject(name, video);
    } catch (err) {
      if (err.status === 409) {
        // Project already exists — load it from the 409 response body
        const existing = err.data;
        const sanitized = (existing.project_name || name).replace(/\s+/g, '_');
        const data = await getClips(sanitized);
        currentProject = sanitized;
        hideSetupArea();
        showProjectHeader(existing.project_name || name, data.clips.length);
        renderClips(data.clips);
        return;
      }
      throw err;
    }

    currentProject = project.project_name.replace(/\s+/g, '_');
    hideSetupArea();
    showProjectHeader(project.project_name, 0);
    renderClips([]);
  } catch (err) {
    showError(`Failed to load project: ${err.message}`);
  } finally {
    loadBtn.disabled = false;
    loadBtn.textContent = 'Load / Create Project';
  }
});

// ── Split video ───────────────────────────────────────────
splitBtn.addEventListener('click', async () => {
  clearError();
  splitBtn.disabled = true;
  showLog();
  appendLog('Starting split — this may take several minutes for long tapes…');
  appendLog('Running FFmpeg (black-frame + scene detection)…');
  startDots(splitBtn, '⏳ Splitting');
  startElapsed();

  try {
    const result = await splitVideo(currentProject);
    if (result.stderr) appendLog(`Warning: ${result.stderr}`);
    const sc = result.split_counts || {};
    if (sc.blackdetect !== undefined)
      appendLog(`  black-frame cuts: ${sc.blackdetect}  scene-score cuts: ${sc.scene}`);
    appendLog(`✅ Done! ${result.clip_count} clips ready.`);
    // Fetch clips through the normal endpoint so thumbnail/stream URLs are populated
    const data = await getClips(currentProject);
    renderClips(data.clips);
  } catch (err) {
    appendLog(`❌ Error: ${err.message}`);
    showError(`Split failed: ${err.message}`);
  } finally {
    stopElapsed();
    stopDots(splitBtn, '▶ Split Video');
    splitBtn.disabled = false;
  }
});

// ── Publish DVD ───────────────────────────────────────────
publishBtn.addEventListener('click', async () => {
  clearError();
  publishBtn.disabled = true;
  publishBtn.textContent = '⏳ Publishing…';
  showLog();
  appendLog('Starting DVD authoring pipeline…');

  try {
    const result = await publishDVD(currentProject);
    appendLog('Building menu video…');
    appendLog('Running dvdauthor…');
    appendLog('Creating ISO…');
    appendLog(`✅ Done! Output: ${result.iso_path || 'see output/ folder'}`);
  } catch (err) {
    appendLog(`❌ Error: ${err.message}`);
    showError(`Publish failed: ${err.message}`);
    publishBtn.disabled = false;
    publishBtn.textContent = '🔴 Publish DVD';
  }
});

// ── On load ───────────────────────────────────────────────
checkSettings();
loadRecentProjects();
