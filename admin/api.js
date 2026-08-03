class AdminApiError extends Error {
  constructor(message, { status = 0, code = "request_failed" } = {}) {
    super(message);
    this.name = "AdminApiError";
    this.status = status;
    this.code = code;
  }
}

export async function requestAdminApi(
  path,
  options = {},
  fetchImplementation = window.fetch.bind(window),
) {
  const {
    body,
    headers: additionalHeaders = {},
    ...requestOptions
  } = options;
  const headers = {
    "X-Grapyth-Admin": "1",
    ...additionalHeaders,
  };

  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    requestOptions.body = JSON.stringify(body);
  }

  const response = await fetchImplementation(path, {
    ...requestOptions,
    headers,
  });
  const payload = await response.json().catch(() => ({}));

  if (!response.ok) {
    throw new AdminApiError(
      payload.detail || payload.error?.message || "Request failed",
      {
        status: response.status,
        code: payload.errorCode || payload.error?.code || "request_failed",
      },
    );
  }

  return payload;
}
