import { useEffect, useState } from "react";

export interface Resource<T> {
  data: T | null;
  error: Error | null;
  /** True while the current request is in flight. Previous data stays available so the frame holds. */
  loading: boolean;
}

interface Settled<T> {
  key: string | null;
  data: T | null;
  error: Error | null;
}

/**
 * Fetches when `deps` change, aborting the previous request. The previous result is kept while
 * the new one loads (the page dims it instead of flashing a skeleton). ``loading`` is derived:
 * the request for the current deps has not settled yet.
 */
export function useResource<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): Resource<T> {
  const key = JSON.stringify(deps);
  const [settled, setSettled] = useState<Settled<T>>({ key: null, data: null, error: null });

  useEffect(() => {
    const controller = new AbortController();
    fetcher(controller.signal)
      .then((data) => setSettled({ key, data, error: null }))
      .catch((e: unknown) => {
        if (controller.signal.aborted) return;
        setSettled((s) => ({
          key,
          data: s.data,
          error: e instanceof Error ? e : new Error("Unknown error"),
        }));
      });
    return () => controller.abort();
    // The caller supplies the dependency list (the fetcher is recreated on every render).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return {
    data: settled.data,
    error: settled.key === key ? settled.error : null,
    loading: settled.key !== key,
  };
}
