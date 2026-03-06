// Fetch latest release from GitHub and wire up download button
const REPO = 'elijahfinlay/finlaycut';
const API_URL = `https://api.github.com/repos/${REPO}/releases/latest`;

let downloadUrl = `https://github.com/${REPO}/releases`;
let latestVersion = null;

async function fetchLatestRelease() {
  const versionTag = document.getElementById('versionTag');
  try {
    const res = await fetch(API_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    latestVersion = data.tag_name;

    // Find the .dmg asset (prefer arm64, fall back to any .dmg)
    const dmgArm = data.assets.find(a => a.name.endsWith('.dmg') && a.name.includes('arm64'));
    const dmgAny = data.assets.find(a => a.name.endsWith('.dmg'));
    const zipAny = data.assets.find(a => a.name.endsWith('.zip'));
    const asset = dmgArm || dmgAny || zipAny;

    if (asset) {
      downloadUrl = asset.browser_download_url;
    } else if (data.assets.length > 0) {
      downloadUrl = data.assets[0].browser_download_url;
    } else {
      downloadUrl = data.html_url;
    }

    versionTag.textContent = `${latestVersion} — Latest Release`;
    versionTag.classList.add('loaded');
  } catch (e) {
    versionTag.textContent = 'Visit GitHub for latest release';
    versionTag.classList.add('loaded');
    downloadUrl = `https://github.com/${REPO}/releases`;
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
