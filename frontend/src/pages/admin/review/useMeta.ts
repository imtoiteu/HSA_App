import { useEffect, useState } from "react";
import { get } from "../../../lib/api";
import type { Meta } from "./rtypes";

let cached: Promise<Meta> | null = null;

/** Review Studio reference data (role/permissions, subjects, reviewers, queue labels), loaded once per page load. */
export function useMeta(): Meta | null {
  const [m, setM] = useState<Meta | null>(null);
  useEffect(() => {
    if (!cached) cached = get<Meta>("/api/review/meta").catch((e) => { cached = null; throw e; });
    let alive = true;
    cached.then((x) => alive && setM(x)).catch(() => {});
    return () => { alive = false; };
  }, []);
  return m;
}
