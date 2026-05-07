/**
 * Cerebrum Universal Adapter Client
 * Enforces: ALL block execution goes through /v1/execute
 */

const DEFAULT_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

export type ApiError = {
  status: number;
  message: string;
  body?: any;
};

export class CerebrumClient {
  private base: string;
  private key: string;

  constructor(apiKey: string = import.meta.env.VITE_API_KEY || "cb_dev_key", baseUrl: string = DEFAULT_BASE) {
    this.base = baseUrl.replace(/\/$/, "");
    this.key = apiKey;
  }

  setKey(apiKey: string) {
    this.key = apiKey;
  }

  private headers(contentType = true): Record<string, string> {
    const h: Record<string, string> = {
      Authorization: `Bearer ${this.key}`,
    };
    if (contentType) h["Content-Type"] = "application/json";
    return h;
  }

  private async handle<T>(response: Response): Promise<T> {
    if (!response.ok) {
      const text = await response.text().catch(() => "Unknown error");
      let body: any;
      try {
        body = JSON.parse(text);
      } catch {
        body = text;
      }
      const err: ApiError = {
        status: response.status,
        message: body?.detail || body?.error || text || `HTTP ${response.status}`,
        body,
      };
      throw err;
    }
    if (response.status === 204) return undefined as T;
    return response.json() as Promise<T>;
  }

  async get<T>(path: string): Promise<T> {
    const res = await fetch(`${this.base}${path}`, {
      method: "GET",
      headers: this.headers(),
    });
    return this.handle<T>(res);
  }

  async post<T>(path: string, body: any): Promise<T> {
    const res = await fetch(`${this.base}${path}`, {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify(body),
    });
    return this.handle<T>(res);
  }

  async del<T>(path: string): Promise<T> {
    const res = await fetch(`${this.base}${path}`, {
      method: "DELETE",
      headers: this.headers(),
    });
    return this.handle<T>(res);
  }

  // ─────────────────────────────────────────────────────────────
  // UNIVERSAL BLOCK ADAPTER
  // ─────────────────────────────────────────────────────────────

  async execute(block: string, input?: any, params?: Record<string, any>): Promise<any> {
    return this.post("/v1/execute", { block, input, params });
  }

  async chain(steps: Array<{ block: string; input?: any; params?: Record<string, any> }>, initialInput?: any): Promise<any> {
    return this.post("/v1/chain", { steps, initial_input: initialInput });
  }

  // ─────────────────────────────────────────────────────────────
  // FIRST-CLASS ENDPOINTS (supported natively by backend)
  // ─────────────────────────────────────────────────────────────

  async chat(message: string, model = "deepseek-chat", stream = false): Promise<any> {
    return this.post("/v1/chat", { message, model, stream });
  }

  async chatStream(message: string, model = "deepseek-chat"): Promise<Response> {
    return fetch(`${this.base}/v1/chat/stream`, {
      method: "POST",
      headers: this.headers(),
      body: JSON.stringify({ message, model, stream: true }),
    });
  }

  async upload(file: File): Promise<any> {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${this.base}/v1/upload`, {
      method: "POST",
      headers: { Authorization: `Bearer ${this.key}` },
      body: form,
    });
    return this.handle(res);
  }

  // ─────────────────────────────────────────────────────────────
  // SYSTEM
  // ─────────────────────────────────────────────────────────────

  health() {
    return this.get<any>("/v1/health");
  }

  systemHealth() {
    return this.get<any>("/v1/system/health");
  }

  stats() {
    return this.get<any>("/stats");
  }

  listBlocks() {
    return this.get<any>("/v1/blocks");
  }

  blockInfo(name: string) {
    return this.get<any>(`/v1/blocks/${name}`);
  }

  // ─────────────────────────────────────────────────────────────
  // AUTH
  // ─────────────────────────────────────────────────────────────

  async validateKey(apiKey: string) {
    return this.post<any>("/v1/auth/validate", { api_key: apiKey });
  }

  async listKeys(adminKey?: string) {
    const q = adminKey ? `?admin_key=${encodeURIComponent(adminKey)}` : "";
    return this.get<any>(`/v1/auth/keys${q}`);
  }

  async createKey(name: string, role: string, owner?: string) {
    return this.post<any>("/v1/auth/keys", { name, role, owner });
  }

  async revokeKey(apiKey: string) {
    return this.post<any>("/v1/auth/keys/revoke", { api_key: apiKey });
  }

  async deleteKey(apiKey: string) {
    return this.del<any>(`/v1/auth/keys/${encodeURIComponent(apiKey)}`);
  }

  async rotateKey(apiKey: string) {
    return this.post<any>("/v1/auth/keys/rotate", { api_key: apiKey });
  }

  async checkPermission(apiKey: string, block: string) {
    return this.post<any>("/v1/auth/check", { api_key: apiKey, block });
  }

  async keyUsage(apiKey: string) {
    return this.get<any>(`/v1/auth/usage?api_key=${encodeURIComponent(apiKey)}`);
  }

  // ─────────────────────────────────────────────────────────────
  // MEMORY
  // ─────────────────────────────────────────────────────────────

  memoryStats() {
    return this.get<any>("/v1/memory/stats");
  }

  memoryGet(key: string) {
    return this.post<any>("/v1/memory/get", { key });
  }

  memorySet(key: string, value: any, ttl?: number) {
    return this.post<any>("/v1/memory/set", { key, value, ttl });
  }

  memoryDelete(key: string) {
    return this.post<any>("/v1/memory/delete", { key });
  }

  memoryFlush() {
    return this.post<any>("/v1/memory/flush", {});
  }

  memoryKeys() {
    return this.post<any>("/v1/memory/keys", {});
  }

  memoryExists(key: string) {
    return this.post<any>("/v1/memory/exists", { key });
  }

  // ─────────────────────────────────────────────────────────────
  // MONITORING
  // ─────────────────────────────────────────────────────────────

  leaderboard() {
    return this.get<any>("/v1/leaderboard");
  }

  recommend() {
    return this.get<any>("/v1/recommend");
  }

  predict() {
    return this.get<any>("/v1/predict");
  }

  recordMetrics(provider: string, success: boolean, latencyMs?: number) {
    return this.post<any>("/v1/metrics/record", { provider, success, latency_ms: latencyMs });
  }
}

// Singleton for apps that prefer global instance
export const API = new CerebrumClient();
