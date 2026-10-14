// Installation as an app (PWA): registers the service worker (sw.js) and
// offers an install button where the browser allows it. iOS Safari has no
// install prompt, so it gets a one-line hint instead.

/** True on iPhone / iPad Safari outside an installed app (no install prompt there). */
export function needsIosInstallHint(userAgent, maxTouchPoints, standalone) {
  if (standalone) return false;
  // iPadOS reports itself as a Mac; touch support tells them apart.
  return /iPhone|iPad|iPod/.test(userAgent) || (/Macintosh/.test(userAgent) && maxTouchPoints > 1);
}

if (typeof document !== "undefined") {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("sw.js").catch(() => {
      // The page works the same without it.
    });
  }

  const standalone =
    matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const button = document.getElementById("install-button");
  let deferredPrompt = null;

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredPrompt = event;
    button.hidden = false;
  });
  button.addEventListener("click", async () => {
    if (!deferredPrompt) return;
    deferredPrompt.prompt();
    await deferredPrompt.userChoice;
    deferredPrompt = null;
    button.hidden = true;
  });
  window.addEventListener("appinstalled", () => {
    button.hidden = true;
  });

  document.getElementById("install-ios").hidden = !needsIosInstallHint(
    navigator.userAgent,
    navigator.maxTouchPoints,
    standalone,
  );
}
