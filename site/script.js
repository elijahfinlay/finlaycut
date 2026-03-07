const REPO = 'elijahfinlay/finlaycut';
const API_URL = `https://api.github.com/repos/${REPO}/releases/latest`;

let downloadUrl = `https://github.com/${REPO}/releases`;
let latestVersion = null;
let downloadLabel = 'Choose Download';

function detectPreferredArch() {
  const uaData = navigator.userAgentData;
  const archHint = `${uaData?.architecture || ''} ${navigator.userAgent || ''}`.toLowerCase();

  if (/arm64|aarch64|apple[- ]?silicon/.test(archHint)) {
    return 'arm64';
  }
  if (/x86_64|amd64|intel/.test(archHint)) {
    return 'x64';
  }
  return null;
}

function isArmAsset(name) {
  return /arm64|aarch64|apple[- ]?silicon/i.test(name);
}

function isIntelAsset(name) {
  return /x64|amd64|intel/i.test(name);
}

function pickReleaseAsset(assets) {
  const preferredArch = detectPreferredArch();
  const dmgAssets = assets.filter((asset) => asset.name.endsWith('.dmg'));
  const zipAssets = assets.filter((asset) => asset.name.endsWith('.zip'));

  if (preferredArch === 'arm64') {
    return dmgAssets.find((asset) => isArmAsset(asset.name))
      || zipAssets.find((asset) => isArmAsset(asset.name));
  }

  if (preferredArch === 'x64') {
    return dmgAssets.find((asset) => isIntelAsset(asset.name))
      || zipAssets.find((asset) => isIntelAsset(asset.name));
  }

  return null;
}

function updateDownloadButton() {
  const downloadText = document.getElementById('downloadText');
  if (downloadText) {
    downloadText.textContent = downloadLabel;
  }
}

async function fetchLatestRelease() {
  const versionTag = document.getElementById('versionTag');
  try {
    const res = await fetch(API_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    latestVersion = data.tag_name;

    const asset = pickReleaseAsset(data.assets || []);

    if (asset) {
      downloadUrl = asset.browser_download_url;
      downloadLabel = isArmAsset(asset.name)
        ? 'Download for Apple Silicon'
        : isIntelAsset(asset.name)
          ? 'Download for Intel Mac'
          : 'Download Latest Release';
    } else if (data.assets.length > 0) {
      downloadUrl = data.html_url;
      downloadLabel = 'Choose Download';
    } else {
      downloadUrl = data.html_url;
      downloadLabel = 'View Releases';
    }

    versionTag.textContent = `${latestVersion} — Latest Release`;
    versionTag.classList.add('loaded');
    updateDownloadButton();
  } catch (e) {
    versionTag.textContent = 'Visit GitHub for latest release';
    versionTag.classList.add('loaded');
    downloadUrl = `https://github.com/${REPO}/releases`;
    downloadLabel = 'View Releases';
    updateDownloadButton();
  }
}

function handleDownload() {
  window.open(downloadUrl, '_blank');
}

fetchLatestRelease();

// Intersection Observer for scroll animations
const observerOptions = { threshold: 0.15, rootMargin: '0px 0px -40px 0px' };
const observer = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.classList.add('in-view');
      observer.unobserve(entry.target);
    }
  });
}, observerOptions);

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.feature-card, .step, .req-card, .section-header, .cta-content').forEach(el => {
    observer.observe(el);
  });
});

// Nav background on scroll
const nav = document.querySelector('.nav');
window.addEventListener('scroll', () => {
  nav.classList.toggle('scrolled', window.scrollY > 40);
}, { passive: true });
