import { useCallback, useEffect, useState } from 'react';

/* Liten data-fetching-hook utan beroenden. Hanterar loading/error och
 * exponerar refetch. För Commit 6 kan vi byta ut mot något större (t.ex.
 * TanStack Query) — men för nu räcker det här. */

export function useApiData(loader, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(true);

  const run = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await loader();
      setData(result);
    } catch (err) {
      setError(err);
    } finally {
      setIsLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    let cancelled = false;
    run().catch(() => {});
    return () => {
      cancelled = true;
      void cancelled;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run]);

  return { data, error, isLoading, refetch: run };
}
