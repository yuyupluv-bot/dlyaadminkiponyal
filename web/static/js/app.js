(() => {
  // This file is browser-only. The guard prevents a deployment runner from
  // crashing if it mistakenly evaluates static assets in Node.js.
  if (typeof document === "undefined") return;

  document.addEventListener("click", (event) => {
    const sidebarToggle = event.target.closest("[data-sidebar-toggle]");
    if (sidebarToggle) {
      document.querySelector(".sidebar")?.classList.toggle("open");
    }

    const tab = event.target.closest("[data-tab]");
    if (!tab) return;

    document.querySelectorAll("[data-tab]").forEach((item) => {
      item.classList.toggle("active", item === tab);
    });
    document.querySelectorAll("[data-pane]").forEach((pane) => {
      pane.classList.toggle("active", pane.dataset.pane === tab.dataset.tab);
    });
    if (typeof localStorage !== "undefined") {
      localStorage.setItem("settingsTab", tab.dataset.tab);
    }
  });

  document.addEventListener("DOMContentLoaded", () => {
    if (typeof localStorage === "undefined") return;
    const saved = localStorage.getItem("settingsTab");
    if (saved) document.querySelector(`[data-tab="${saved}"]`)?.click();
  });
})();
