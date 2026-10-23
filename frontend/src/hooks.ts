import { useCallback, useEffect, useState } from 'react';

export function useRequest<T>(request: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  /* eslint-disable react-hooks/exhaustive-deps -- This hook intentionally accepts a caller-owned dependency list, mirroring React hooks. */
  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await request());
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }, deps);
  /* eslint-enable react-hooks/exhaustive-deps */

  useEffect(() => {
    void run();
  }, [run]);

  return { data, loading, error, retry: run };
}
