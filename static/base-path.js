// Root-relative API/media URLs stay portable; this shim scopes them to the
// directory from which this script was loaded (or leaves root deployments alone).
((root) => {
  function derivePrefix(scriptUrl) {
    try {
      const path = new URL(scriptUrl, root?.location?.href || 'http://localhost/').pathname;
      return path.replace(/\/base-path\.js$/, '').replace(/\/$/, '');
    } catch { return ''; }
  }

  function addPrefix(value, prefix, locationLike, URLImpl = URL, base) {
    if (value instanceof URLImpl) value = value.href;
    if (!prefix || typeof value !== 'string' || !value || value.startsWith('//') || value.startsWith('#') ||
        /^(data|blob|javascript|mailto|tel):/i.test(value)) return value;
    let url;
    try { url = new URLImpl(value, base || locationLike.href); } catch { return value; }
    const sameHost = url.origin === locationLike.origin ||
      (url.host === locationLike.host && (
        (url.protocol === 'wss:' && locationLike.protocol === 'https:') ||
        (url.protocol === 'ws:' && locationLike.protocol === 'http:')
      ));
    if (!sameHost || url.pathname === prefix || url.pathname.startsWith(prefix + '/')) return value;
    url.pathname = prefix + url.pathname;
    if (value.startsWith('/')) return url.pathname + url.search + url.hash;
    if (/^(https?:|wss?:)/i.test(value)) return url.href;
    return value;
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { derivePrefix, addPrefix };
    return;
  }
  if (!root?.location || !root.document) return;
  const scriptUrl = root.document.currentScript?.src;
  const prefix = derivePrefix(scriptUrl || '');
  const locationLike = root.location;
  const NativeURL = root.URL;
  const scoped = (value, base) => addPrefix(value, prefix, locationLike, NativeURL, base);
  root.appUrl = (path) => scoped(path);
  if (!prefix) return;

  window.fetch = ((nativeFetch) => (input, init) => {
    if (input instanceof Request) {
      const url = scoped(input.url);
      return nativeFetch(url === input.url ? input : new Request(url, input), init);
    }
    return nativeFetch(scoped(input), init);
  })(window.fetch.bind(window));

  window.URL = new Proxy(NativeURL, {
    construct(target, args) {
      if (args.length) args[0] = scoped(args[0], args[1] || locationLike.href);
      return Reflect.construct(target, args, target);
    },
  });

  for (const [prototype, property] of [
    [HTMLImageElement.prototype, 'src'], [HTMLMediaElement.prototype, 'src'],
    [HTMLScriptElement.prototype, 'src'], [HTMLLinkElement.prototype, 'href'],
    [HTMLAnchorElement.prototype, 'href'], [HTMLFormElement.prototype, 'action'],
  ]) {
    const descriptor = Object.getOwnPropertyDescriptor(prototype, property);
    if (!descriptor || !descriptor.set || !descriptor.configurable) continue;
    Object.defineProperty(prototype, property, {
      ...descriptor,
      set(value) { descriptor.set.call(this, scoped(value)); },
    });
  }

  for (const name of ['WebSocket', 'EventSource']) {
    const Native = window[name];
    if (!Native) continue;
    window[name] = new Proxy(Native, {
      construct(target, args) {
        args[0] = scoped(args[0]);
        return Reflect.construct(target, args, target);
      },
    });
  }
})(typeof window !== 'undefined' ? window : null);
