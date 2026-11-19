((root) => {
  const ios = () => /iPad|iPhone|iPod/.test(root.navigator?.userAgent || "") ||
    (root.navigator?.platform === "MacIntel" && root.navigator?.maxTouchPoints > 1);
  const standalone = () => root.navigator?.standalone === true || root.matchMedia?.("(display-mode: standalone)").matches;
  // Declarative Web Push (Safari 18.4+) exposes window.pushManager and needs no service worker.
  const declarative = () => Boolean(root.pushManager?.subscribe);
  const supported = () => Boolean(root.Notification && (declarative() || (root.navigator?.serviceWorker && root.PushManager)));
  const state = async () => {
    if (!supported()) return "unsupported";
    if (ios() && !standalone()) return "needs-install";
    if (root.Notification.permission === "denied") return "denied";
    if (root.Notification.permission === "default") return "default";
    const reg = declarative() ? null : await root.navigator.serviceWorker.getRegistration(new URL("./", root.location.href));
    const manager = declarative() ? root.pushManager : reg?.pushManager;
    return manager && await manager.getSubscription() ? "subscribed" : "default";
  };
  const api = async (path, options = {}) => {
    const response = await root.fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
  };
  const decodeKey = (key) => Uint8Array.from(atob(key.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
  const enable = async () => {
    if (!supported()) throw new Error("Push notifications are not supported in this browser.");
    if (ios() && !standalone()) throw new Error("Add to Home Screen first (Share > Add to Home Screen), then open from the icon.");
    const permission = await root.Notification.requestPermission();
    if (permission !== "granted") throw new Error("Notification permission was not granted.");
    // Browsers with Declarative Web Push need no worker; elsewhere the worker renders the same payload.
    const registration = declarative() ? null : await root.navigator.serviceWorker.register("./sw.js");
    const config = await api("/api/push/config");
    if (!config.enabled || !config.public_key) throw new Error(config.reason || "Push notifications are unavailable.");
    const manager = declarative() ? root.pushManager : registration.pushManager;
    const subscription = await manager.subscribe({ userVisibleOnly: true, applicationServerKey: decodeKey(config.public_key) });
    await api("/api/push/subscribe", { method: "POST", body: JSON.stringify({ subscription, label: (root.navigator.userAgent || "Browser").slice(0, 80), base_url: new URL("./", root.location.href).href }) });
    return state();
  };
  const disable = async () => {
    const registration = declarative() ? null : await root.navigator.serviceWorker.getRegistration(new URL("./", root.location.href));
    const manager = declarative() ? root.pushManager : registration?.pushManager;
    const subscription = manager && await manager.getSubscription();
    if (subscription) {
      await api("/api/push/unsubscribe", { method: "POST", body: JSON.stringify({ endpoint: subscription.endpoint }) });
      await subscription.unsubscribe();
    }
    return state();
  };
  const test = () => api("/api/push/test", { method: "POST", body: "{}" });
  root.AgentSlackPush = { supported, declarative, state, enable, disable, test };
})(typeof window !== "undefined" ? window : globalThis);
