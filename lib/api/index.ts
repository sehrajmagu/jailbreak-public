import type { ApiClient } from "@/lib/api/client";
import { httpApi } from "@/lib/api/http";
import { mockApi } from "@/lib/api/mock/client";

export const api: ApiClient =
  process.env.NEXT_PUBLIC_USE_MOCK === "true" ? mockApi : httpApi;

export { ApiError } from "@/lib/api/errors";
