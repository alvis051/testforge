/** The platform's error contract: every backend error is {code, message, details}. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function apiFetch<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
  });

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    const parsed = body as
      | { code?: string; message?: string; details?: Record<string, unknown> }
      | null;
    throw new ApiError(
      response.status,
      parsed?.code ?? "unknown_error",
      parsed?.message || response.statusText || `HTTP ${response.status}`,
      parsed?.details ?? {},
    );
  }

  let data: unknown;
  try {
    data = await response.json();
  } catch {
    throw new ApiError(response.status, "invalid_response", "Response was not valid JSON", {});
  }
  return data as T;
}
