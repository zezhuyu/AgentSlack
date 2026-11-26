(function initMobile(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.AgentSlackMobile = api;
  if (root && root.document) root.document.addEventListener("DOMContentLoaded", () => api.init(root));
}(typeof window !== "undefined" ? window : globalThis, function createMobileHelpers() {
  const DRAWER_MAX = 639;
  const KEYBOARD_MIN_PX = 120;
  const TEXT_FIELD = "textarea, input:not([type=checkbox]):not([type=radio]):not([type=range]):not([type=button]):not([type=submit])";

  function isKeyboardOpen(layoutHeight, visibleHeight) {
    return layoutHeight - visibleHeight > KEYBOARD_MIN_PX;
  }

  function isDrawerViewport(widthOrWindow) {
    const width = typeof widthOrWindow === "number" ? widthOrWindow : widthOrWindow?.innerWidth;
    return Number(width) <= DRAWER_MAX;
  }

  function fitHeight(contentHeight, maxHeight) {
    return Math.min(Math.max(contentHeight, 0), maxHeight);
  }

  function isNearBottom(el, slack = 80) {
    return el.scrollHeight - el.scrollTop - el.clientHeight < slack;
  }

  function setupViewport(win) {
    const doc = win.document;
    const rootEl = doc.documentElement;
    const vv = win.visualViewport;
    const scrollers = () => [...doc.querySelectorAll(".messages")];
    let frame = 0;

    const apply = () => {
      frame = 0;
      const height = vv ? vv.height : win.innerHeight;
      const top = vv ? vv.offsetTop : 0;
      const pinned = scrollers().filter((el) => el.clientHeight && isNearBottom(el));
      rootEl.style.setProperty("--vvh", `${height}px`);
      rootEl.style.setProperty("--vv-top", `${top}px`);
      rootEl.classList.toggle("keyboard-open", isKeyboardOpen(win.innerHeight, height));
      // keep the newest message visible when the keyboard shrinks the chat area
      pinned.forEach((el) => { el.scrollTop = el.scrollHeight; });
    };
    const schedule = () => { if (!frame) frame = win.requestAnimationFrame(apply); };

    apply();
    if (vv) {
      vv.addEventListener("resize", schedule);
      vv.addEventListener("scroll", schedule);
    }
    win.addEventListener("orientationchange", schedule);

    // iOS pans the page to reveal a focused field; pin it back so the composer
    // rides on top of the keyboard instead of the page zooming or scrolling.
    doc.addEventListener("focusin", (event) => {
      if (!event.target.matches?.(TEXT_FIELD)) return;
      [0, 120, 320].forEach((delay) => win.setTimeout(() => {
        win.scrollTo(0, 0);
        schedule();
      }, delay));
    });
  }

  function setupAutosize(win) {
    const doc = win.document;
    const descriptor = Object.getOwnPropertyDescriptor(win.HTMLTextAreaElement.prototype, "value");
    doc.querySelectorAll(".composer textarea").forEach((textarea) => {
      const fit = () => {
        textarea.style.height = "auto";
        const max = parseFloat(win.getComputedStyle(textarea).maxHeight) || Infinity;
        textarea.style.height = `${fitHeight(textarea.scrollHeight, max)}px`;
        textarea.style.overflowY = textarea.scrollHeight > max ? "auto" : "hidden";
      };
      // app code clears or restores the draft through .value, which fires no input event
      Object.defineProperty(textarea, "value", {
        get() { return descriptor.get.call(this); },
        set(next) { descriptor.set.call(this, next); fit(); },
      });
      textarea.addEventListener("input", fit);
      fit();
    });
  }

  function setupObjectiveToggle(win) {
    const doc = win.document;
    const button = doc.getElementById("objectiveToggleBtn");
    const input = doc.getElementById("objectiveInput");
    if (!button || !input) return;
    const sync = () => {
      button.setAttribute("aria-expanded", String(!input.hidden));
      button.classList.toggle("is-on", !input.hidden || Boolean(input.value.trim()));
    };
    button.addEventListener("click", () => {
      input.hidden = !input.hidden;
      if (!input.hidden) input.focus();
      sync();
    });
    input.addEventListener("input", sync);
    sync();
  }

  function setupActionsMenu(win) {
    const doc = win.document;
    const button = doc.getElementById("chatMoreBtn");
    const menu = doc.getElementById("chatActionsMenu");
    if (!button || !menu) return;
    const setOpen = (open) => {
      menu.classList.toggle("open", open);
      button.setAttribute("aria-expanded", String(open));
    };
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      setOpen(!menu.classList.contains("open"));
    });
    menu.addEventListener("click", () => setOpen(false));
    doc.addEventListener("click", (event) => {
      if (!menu.contains(event.target)) setOpen(false);
    });
    doc.addEventListener("keydown", (event) => {
      if (event.key === "Escape") setOpen(false);
    });
  }

  function init(win) {
    setupViewport(win);
    setupAutosize(win);
    setupObjectiveToggle(win);
    setupActionsMenu(win);
  }

  return { init, isKeyboardOpen, isDrawerViewport, fitHeight, isNearBottom };
}));
