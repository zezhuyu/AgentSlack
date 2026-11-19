/* Web Push fallback for browsers without Declarative Web Push support. */
self.addEventListener("push", (event) => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch { payload = {}; }
  const item = payload.notification || {};
  const title = item.title || "Agent Slack";
  const options = {
    body: item.body || "An agent run has finished.",
    tag: item.tag || "agent-slack",
    silent: item.silent === true,
    data: { navigate: item.navigate || self.registration.scope },
  };
  event.waitUntil(Promise.all([
    self.registration.showNotification(title, options),
    payload.app_badge !== undefined && self.navigator?.setAppBadge
      ? self.navigator.setAppBadge(payload.app_badge).catch(() => {}) : Promise.resolve(),
  ]));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = event.notification.data?.navigate || self.registration.scope;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
    const existing = clients.find((client) => "focus" in client);
    return existing
      ? existing.focus().then(() => existing.navigate ? existing.navigate(target) : existing).catch(() => self.clients.openWindow(target))
      : self.clients.openWindow(target);
  }));
});

self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil((async () => {
    try {
      const response = await fetch(new URL("api/push/config", self.registration.scope), { credentials: "include" });
      const config = await response.json();
      if (!config.enabled || !config.public_key) return;
      const key = Uint8Array.from(atob(config.public_key.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
      const subscription = await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
      await fetch(new URL("api/push/subscribe", self.registration.scope), {
        method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subscription, base_url: self.registration.scope, label: "Service worker renewal" }),
      });
    } catch { /* best effort: the next foreground visit can re-enable push */ }
  })());
});
