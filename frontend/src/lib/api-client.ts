import type {
  AnalysisEvent,
  CreateIncidentRequest,
  IncidentDetail,
  IncidentSummary,
  MemoryReference,
  ResolveResponse,
  ResolveIncidentRequest,
  StatsResponse,
} from "./memory-types";

const API_BASE_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  readonly status: number | null;
  readonly code: string | null;
  readonly offline: boolean;

  constructor(
    message: string,
    status: number | null = null,
    code: string | null = null,
    offline = false,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.offline = offline;
  }
}

export function isApiOfflineError(error: unknown): error is ApiError {
  return error instanceof ApiError && error.offline;
}

type ErrorBody = {
  detail?: unknown;
  message?: unknown;
  error?: unknown;
};

function errorDetails(body: ErrorBody, fallback: string): { message: string; code: string | null } {
  const nested = body.error && typeof body.error === "object" ? body.error as Record<string, unknown> : null;
  const code = typeof nested?.code === "string" ? nested.code : null;
  const detail = body.detail;
  const detailMessage = typeof detail === "string"
    ? detail
    : Array.isArray(detail)
      ? detail.map((item) => item && typeof item === "object" && "msg" in item ? String(item.msg) : "").filter(Boolean).join("; ")
      : null;
  const message = detailMessage
    || (typeof body.message === "string" ? body.message : "")
    || (typeof nested?.message === "string" ? nested.message : "")
    || (typeof body.error === "string" ? body.error : "")
    || (code ? `API error: ${code}` : fallback);
  return { message: code && !message.includes(code) ? `${message} (${code})` : message, code };
}

async function responseError(response: Response, fallback: string): Promise<ApiError> {
  try {
    const body = await response.json() as ErrorBody;
    const details = errorDetails(body, fallback);
    return new ApiError(details.message, response.status, details.code);
  } catch {
    return new ApiError(fallback, response.status);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("The Aftermath API could not be reached. Check the API URL and connection.", null, null, true);
  }

  if (!response.ok) {
    throw await responseError(response, `The API request failed (${response.status}).`);
  }

  return response.json() as Promise<T>;
}

export function getIncidents(signal?: AbortSignal): Promise<IncidentSummary[]> {
  return request<IncidentSummary[]>("/incidents?limit=100", { signal });
}

export function getIncident(id: string, signal?: AbortSignal): Promise<IncidentDetail> {
  return request<IncidentDetail>(`/incidents/${encodeURIComponent(id)}`, { signal });
}

export function getIncidentMemories(id: string, signal?: AbortSignal): Promise<MemoryReference[]> {
  return request<MemoryReference[]>(`/incidents/${encodeURIComponent(id)}/memories`, { signal });
}

export function getStats(signal?: AbortSignal): Promise<StatsResponse> {
  return request<StatsResponse>("/stats", { signal });
}

export function createIncident(data: CreateIncidentRequest, signal?: AbortSignal): Promise<IncidentDetail> {
  return request<IncidentDetail>("/incidents", {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function resolveIncident(id: string, data: ResolveIncidentRequest, signal?: AbortSignal): Promise<ResolveResponse> {
  return request<ResolveResponse>(`/incidents/${encodeURIComponent(id)}/resolve`, {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

/**
 * Fetch-based SSE is deliberately used instead of EventSource: the API does not
 * support Last-Event-ID replay. A broken stream is surfaced for an explicit
 * restart, avoiding an implicit duplicate analysis on automatic reconnect.
 */
export async function streamIncidentAnalysis(
  id: string,
  signal: AbortSignal,
  onEvent: (event: AnalysisEvent) => void,
): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}/incidents/${encodeURIComponent(id)}/analyze`, {
      method: "GET",
      headers: { Accept: "text/event-stream" },
      cache: "no-store",
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError("The investigation stream could not connect.", null, null, true);
  }

  if (!response.ok) {
    throw await responseError(response, `The investigation request failed (${response.status}).`);
  }
  if (!response.body) throw new ApiError("The investigation response did not include a stream.");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventName = "message";
  let dataLines: string[] = [];
  let doneReceived = false;

  const dispatch = () => {
    if (!dataLines.length) {
      eventName = "message";
      return;
    }
    let event: AnalysisEvent;
    try {
      event = JSON.parse(dataLines.join("\n")) as AnalysisEvent;
    } catch {
      throw new ApiError("The investigation stream returned an invalid event.");
    }
    if (!event || typeof event !== "object" || typeof event.type !== "string") {
      throw new ApiError("The investigation stream returned an unsupported event.");
    }
    if (eventName !== "message" && eventName !== event.type) {
      throw new ApiError("The investigation stream event name did not match its payload.");
    }
    if (event.type === "error") {
      onEvent(event);
      throw new ApiError(event.message || "The investigation agent returned an error.");
    }
    onEvent(event);
    if (event.type === "done") doneReceived = true;
    eventName = "message";
    dataLines = [];
  };

  const processLine = (line: string) => {
    if (line === "") {
      dispatch();
      return;
    }
    if (line.startsWith(":")) return;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    const value = separator === -1 ? "" : line.slice(separator + 1).replace(/^ /, "");
    if (field === "event") eventName = value;
    if (field === "data") dataLines.push(value);
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let newline = buffer.indexOf("\n");
      while (newline !== -1) {
        processLine(buffer.slice(0, newline).replace(/\r$/, ""));
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf("\n");
      }
      if (done) break;
    }
    if (buffer) processLine(buffer.replace(/\r$/, ""));
    dispatch();
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }

  if (!doneReceived) throw new ApiError("The investigation stream ended before completion. Restart it to reconnect.");
}
