export function currentLocation() {
  return {
    path: window.location.pathname.replace(/\/$/, "") || "/",
    query: new URLSearchParams(window.location.search),
  };
}

export function navigate(href) {
  const url = new URL(href, window.location.origin);
  if (url.origin !== window.location.origin) return;
  window.history.pushState({}, "", `${url.pathname}${url.search}`);
  window.dispatchEvent(new Event("dfip:navigate"));
}

export function matchRoute(path, routes) {
  for (const route of routes) {
    const matched = path.match(route.pattern);
    if (matched) {
      return { route, params: matched.groups || {} };
    }
  }
  return null;
}
