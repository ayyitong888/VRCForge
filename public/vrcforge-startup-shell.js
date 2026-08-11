(function scheduleStartupShellPaint() {
  var metrics = (window.__vrcforgeStartupMetrics ||= {});
  metrics.startupShellRequestedMs ??= Math.round(performance.now());
  window.__vrcforgeStartupShellPaintedPromise ??= new Promise(function (resolve) {
    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(function () {
        metrics.startupShellPaintedMs ??= Math.round(performance.now());
        document.documentElement.dataset.vrcforgeStartupShell = "ready";
        resolve();
      });
    });
  });
})();
