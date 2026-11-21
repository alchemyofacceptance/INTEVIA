"use strict";
// Presentation only. No storage, cookies, network calls or business decisions.
(() => {
  const row = document.querySelector(".domain-nav");
  let scrolling = false;
  let timer;
  const editing = () => document.activeElement && document.activeElement.matches("input,textarea,select,[contenteditable=true]");
  const update = () => {
    if (row) row.classList.toggle("collapsed", window.matchMedia("(max-width: 63.999rem)").matches && (editing() || scrolling));
  };
  document.addEventListener("focusin", update);
  document.addEventListener("focusout", () => setTimeout(update, 0));
  // Never hide a row while it owns focus: keyboard users must retain their place.
  document.addEventListener("scroll", () => {
    if (row && row.contains(document.activeElement)) return;
    scrolling = true; update(); clearTimeout(timer);
    timer = setTimeout(() => { scrolling = false; update(); }, 220);
  }, {passive:true, capture:true});
  window.addEventListener("resize", update);
})();
