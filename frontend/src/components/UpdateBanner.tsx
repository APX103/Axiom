// 检查 GitHub Release 是否有更新，若有则展示横幅并引导用户去 Release 页下载。
// 不自动下载/安装，只拉版本号做比对。

import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { version as CURRENT_VERSION } from "../../package.json";

const CHECK_INTERVAL_MS = 1000 * 60 * 60; // 每小时检查一次

export function UpdateBanner() {
  const [update, setUpdate] = useState<{ version: string; url: string } | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let mounted = true;

    const check = async () => {
      try {
        const result = await invoke<[string, string] | null>("check_update", {
          current: CURRENT_VERSION,
        });
        if (mounted && result) {
          setUpdate({ version: result[0], url: result[1] });
        }
      } catch {
        // 网络失败时静默，下次再试
      }
    };

    check();
    const timer = setInterval(check, CHECK_INTERVAL_MS);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);

  if (dismissed || !update) return null;

  return (
    <div className="bg-indigo-50 border-b border-indigo-100 px-4 py-2 text-sm text-indigo-800 flex items-center justify-between">
      <span>
        发现新版本 <strong>{update.version}</strong>，当前版本 {CURRENT_VERSION}。
      </span>
      <div className="flex items-center gap-3">
        <button
          onClick={() =>
            invoke("open_external_url", { url: update.url }).catch((e) =>
              console.error("open_external_url failed:", e)
            )
          }
          className="font-medium underline hover:text-indigo-600"
        >
          去 GitHub 下载
        </button>
        <button
          onClick={() => setDismissed(true)}
          className="text-indigo-400 hover:text-indigo-600"
          aria-label="忽略"
        >
          ✕
        </button>
      </div>
    </div>
  );
}
