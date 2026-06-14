/** Proxy /v1/* and health to Render; serve React SPA from ASSETS binding. */
const ORIGIN = 'https://the-fork.onrender.com'

const PROXY_PREFIXES = ['/v1/', '/health', '/docs', '/openapi.json', '/redoc']

export default {
  async fetch(request, env) {
    const url = new URL(request.url)
    const path = url.pathname

    if (PROXY_PREFIXES.some((p) => path === p || path.startsWith(p))) {
      const target = new URL(path + url.search, ORIGIN)
      const headers = new Headers(request.headers)
      headers.set('Host', new URL(ORIGIN).host)
      return fetch(
        new Request(target.toString(), {
          method: request.method,
          headers,
          body: request.body,
          redirect: 'follow',
        }),
      )
    }

    return env.ASSETS.fetch(request)
  },
}
