// Tauri 桌面壳库入口。
// 启动时自动拉起 Python 后端, 注入后端端口, 关闭时清理进程。
// macOS 上启用系统级毛玻璃(vibrancy)效果。

use std::fs;
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;
use tauri::{Manager, RunEvent, WebviewWindow};
use tokio::process::{Child, Command};
use tokio::sync::Mutex;



struct AppState {
    backend: Arc<Mutex<Option<Child>>>,
    backend_port: Arc<Mutex<u16>>,
}

/// macOS: 把红绿灯 (close/mini/zoom) 向右下内移。
/// tao 自带的 traffic_light_inset 依赖 content view 的 drawRect 重排,
/// 但我们的窗口被不透明的 WKWebView 整个盖住, drawRect 不触发, inset 不生效,
/// 所以这里直接操作 Cocoa。窗口 resize 后 AppKit 会复位按钮位置, 需要在 Resized 事件里重排。
#[cfg(target_os = "macos")]
fn inset_traffic_lights(ns_window_ptr: *mut std::ffi::c_void, x: f64, y: f64) {
    use objc2_app_kit::{NSWindow, NSWindowButton};
    unsafe {
        let ns_window = &*(ns_window_ptr as *const NSWindow);
        let (Some(close), Some(mini), Some(zoom)) = (
            ns_window.standardWindowButton(NSWindowButton::CloseButton),
            ns_window.standardWindowButton(NSWindowButton::MiniaturizeButton),
            ns_window.standardWindowButton(NSWindowButton::ZoomButton),
        ) else {
            return;
        };
        let Some(container) = close.superview().and_then(|v| v.superview()) else {
            return;
        };
        let close_rect = close.frame();
        // y = 按钮顶部到窗口顶部的距离; 通过加高 titlebar 容器把按钮往下推
        let new_height = close_rect.size.height + y;
        let mut container_rect = container.frame();
        container_rect.size.height = new_height;
        container_rect.origin.y = ns_window.frame().size.height - new_height;
        container.setFrame(container_rect);
        // x = close 按钮左缘到窗口左缘的距离, 三颗灯等间距排布
        let spacing = mini.frame().origin.x - close_rect.origin.x;
        for (i, button) in [close, mini, zoom].into_iter().enumerate() {
            let mut rect = button.frame();
            rect.origin.x = x + i as f64 * spacing;
            button.setFrameOrigin(rect.origin);
        }
    }
}

/// 红绿灯目标位置 (逻辑像素, 与前端布局对齐)。
/// 注意: macOS 默认 titlebar 容器高 = 按钮 14 + 系统 inset 18 = 32,
/// 本实现容器高 = 14 + Y, 按钮在容器内位置不变, 所以要下移 D 像素需 Y = 18 + D。
/// Y=26 → 按钮顶部距窗口顶 17px, 中线 y=24 (对齐飞书)。
#[cfg(target_os = "macos")]
const TRAFFIC_LIGHT_X: f64 = 18.0;
#[cfg(target_os = "macos")]
const TRAFFIC_LIGHT_Y: f64 = 26.0;

/// 获取一个稳定的工作目录。
/// 生产环境 bundle 里没有固定 CWD, 所以退回到用户主目录, 避免文件写到 app bundle 里。
fn backend_work_dir() -> PathBuf {
    if let Ok(home) = std::env::var("HOME") {
        PathBuf::from(home)
    } else if let Ok(userprofile) = std::env::var("USERPROFILE") {
        PathBuf::from(userprofile)
    } else {
        std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
    }
}

/// 找一个可用端口 (从 start 开始递增探测)。
fn find_free_port(start: u16) -> u16 {
    for port in start..=65535 {
        if TcpListener::bind(("127.0.0.1", port)).is_ok() {
            return port;
        }
    }
    panic!("no free port found");
}

/// 确保数据目录存在, 返回 ~/.axiom 路径。
///
/// 若 SSD 路径 (/Volumes/ssd/main_link/.axiom) 存在且可写, 通过软链指向它,
/// 避免内置硬盘被数据塞满。SSD 不存在时 fallback 到 ~/.axiom 本地目录。
fn ensure_data_dir() -> PathBuf {
    let home = PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| ".".to_string()));
    let axiom_link = home.join(".axiom");

    // 若已经是软链且指向有效目录, 直接用
    if let Ok(target) = fs::read_link(&axiom_link) {
        if target.is_dir() {
            return axiom_link;
        }
        // 软链指向无效路径, 删掉重建
        let _ = fs::remove_file(&axiom_link);
    }

    // 尝试用 SSD: /Volumes/ssd/main_link/.axiom
    let ssd_parent = PathBuf::from("/Volumes/ssd/main_link");
    let ssd_dir = ssd_parent.join(".axiom");
    if ssd_parent.is_dir() {
        // SSD 存在, 建目录 + 软链
        if let Ok(()) = fs::create_dir_all(&ssd_dir) {
            // 若 ~/.axiom 已是普通目录, 迁移内容到 SSD
            if axiom_link.is_dir() {
                if let Ok(entries) = fs::read_dir(&axiom_link) {
                    for entry in entries.flatten() {
                        let dest = ssd_dir.join(entry.file_name());
                        let _ = fs::rename(entry.path(), dest);
                    }
                }
                let _ = fs::remove_dir(&axiom_link);
            }
            #[cfg(unix)]
            {
                use std::os::unix::fs as unix_fs;
                let _ = unix_fs::symlink(&ssd_dir, &axiom_link);
            }
            if axiom_link.exists() {
                return axiom_link;
            }
        }
    }

    // Fallback: 直接用 ~/.axiom (SSD 不存在或建链失败)
    fs::create_dir_all(&axiom_link).expect("failed to create data dir");
    axiom_link
}

/// 查找后端可执行文件路径。
/// 生产环境: Tauri bundle 的 Resources/operon-backend
/// 开发环境:  fallback 到 python3 -m operon.cli.main serve
fn resolve_backend_binary(app_handle: &tauri::AppHandle) -> (PathBuf, Vec<String>) {
    let resource_dir = app_handle.path().resource_dir().unwrap_or_else(|_| PathBuf::from("."));
    let bundled = resource_dir.join("operon-backend");
    if bundled.is_file() {
        return (bundled, vec!["serve".to_string()]);
    }

    // 开发 fallback
    (
        PathBuf::from("python3"),
        vec!["-m".to_string(), "operon.cli.main".to_string(), "serve".to_string()],
    )
}

/// 把子进程放到独立的进程组, 这样关闭 app 时可以整组杀掉,
/// 避免 PyInstaller onefile 留下孤儿进程继续占端口。
#[cfg(unix)]
fn set_child_pgid(child: &Child) {
    use nix::unistd::{setpgid, Pid};
    if let Some(pid) = child.id() {
        let pid_i32 = pid as i32;
        // 忽略错误: 子进程可能自己已经是组长
        let _ = setpgid(Pid::from_raw(pid_i32), Pid::from_raw(pid_i32));
    }
}

#[cfg(not(unix))]
fn set_child_pgid(_child: &Child) {}

/// 杀掉整个后端进程树 (先 SIGTERM, 再 SIGKILL)。
async fn kill_backend_tree(child: &mut Child) {
    if let Some(pid) = child.id() {
        eprintln!("[axiom] stopping backend pid={}", pid);

        #[cfg(unix)]
        {
            use nix::sys::signal::{killpg, Signal};
            use nix::unistd::Pid;
            let pgid = -(pid as i32);

            // 先优雅终止
            let _ = killpg(Pid::from_raw(pgid), Signal::SIGTERM);

            // 给 500ms  grace period
            tokio::time::sleep(Duration::from_millis(500)).await;

            // 强制杀
            let _ = killpg(Pid::from_raw(pgid), Signal::SIGKILL);
        }

        #[cfg(not(unix))]
        {
            let _ = child.kill().await;
        }
    }

    // 兜底: 直接向子进程发 SIGKILL (处理 process group 设置失败的情况)
    let _ = child.kill().await;
    let _ = child.wait().await;
}

async fn start_backend(app_handle: &tauri::AppHandle, port: u16) -> std::io::Result<Child> {
    let (bin, mut args) = resolve_backend_binary(app_handle);
    let work_dir = backend_work_dir();

    // macOS 上给后端二进制加可执行权限(避免 bundle 复制后权限丢失)
    #[cfg(target_os = "macos")]
    if bin != PathBuf::from("python3") {
        let _ = std::process::Command::new("chmod").arg("+x").arg(&bin).status();
    }

    args.push("--port".to_string());
    args.push(port.to_string());

    let mut cmd = Command::new(&bin);
    let data_dir = ensure_data_dir();
    // 让 Tauri 主进程也持有相同的数据目录，后续 Rust 命令（如打开文件位置）能直接读取
    std::env::set_var("OPERON_DATA_DIR", &data_dir);
    cmd.args(&args)
        .current_dir(&work_dir)
        .env("OPERON_DATA_DIR", {
            eprintln!("[axiom] OPERON_DATA_DIR = {:?}", data_dir);
            data_dir
        })
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .kill_on_drop(true);

    eprintln!("[axiom] starting backend: {:?} {:?} (cwd={:?})", bin, args, work_dir);

    let child = cmd.spawn()?;
    set_child_pgid(&child);

    Ok(child)
}

#[tauri::command]
async fn restart_backend(state: tauri::State<'_, AppState>, app_handle: tauri::AppHandle) -> Result<String, String> {
    let mut guard = state.backend.lock().await;
    if let Some(mut child) = guard.take() {
        kill_backend_tree(&mut child).await;
    }
    let port = *state.backend_port.lock().await;
    match start_backend(&app_handle, port).await {
        Ok(child) => {
            let pid = child.id().unwrap_or(0);
            *guard = Some(child);
            Ok(format!("backend started pid={}", pid))
        }
        Err(e) => Err(format!("failed to start backend: {}", e)),
    }
}

/// release 上附加的 latest.json 元数据。
#[derive(serde::Deserialize)]
struct LatestJson {
    version: String,
    url: String,
}

/// 构造带代理和超时配置的 ureq Agent。
/// 会读取 ALL_PROXY / HTTPS_PROXY / HTTP_PROXY 环境变量。
fn update_agent() -> ureq::Agent {
    use std::time::Duration;
    let mut config = ureq::Agent::config_builder()
        .timeout_global(Some(Duration::from_secs(10)));
    if let Some(proxy) = ureq::Proxy::try_from_env() {
        config = config.proxy(Some(proxy));
    }
    config.build().into()
}

/// 简单的 semver 比较。要求版本字符串以 v 或数字开头。
/// 返回 true 当且仅当 latest > current。
fn is_newer(current: &str, latest: &str) -> bool {
    fn parse(s: &str) -> Vec<u32> {
        s.trim_start_matches('v')
            .split('.')
            .take(3)
            .filter_map(|p| p.parse::<u32>().ok())
            .collect()
    }
    let c = parse(current);
    let l = parse(latest);
    l > c
}

/// 检查 GitHub Release 是否有新版本。
/// 不再直接调 GitHub API, 而是读取 release asset 上的 latest.json,
/// 避免 api.github.com 的 rate limit 和部分网络环境下 API 不可用。
/// 入参 current: 当前版本号 (如 package.json 里的 "0.0.11")。
/// 返回: None = 没有更新; Some((version, url)) = 有新版本及其 Release 页面地址。
#[tauri::command]
fn check_update(current: String) -> Result<Option<(String, String)>, String> {
    const LATEST_JSON_URL: &str =
        "https://github.com/APX103/Axiom/releases/latest/download/latest.json";
    let agent = update_agent();
    let resp = agent
        .get(LATEST_JSON_URL)
        .header("User-Agent", "Axiom-Updater")
        .header("Accept", "application/json")
        .call()
        .map_err(|e| format!("failed to fetch latest.json: {}", e))?;

    let latest: LatestJson = resp
        .into_body()
        .read_json()
        .map_err(|e| format!("failed to parse latest.json: {}", e))?;

    if is_newer(&current, &latest.version) {
        Ok(Some((latest.version, latest.url)))
    } else {
        Ok(None)
    }
}

/// 在系统文件管理器里打开并定位到工作区文件。
///
/// Tauri webview 里 window.open 触发不了下载, 所以"下载"按钮改为
/// 直接打开 Finder (macOS) / Explorer (Win) 定位到该文件。
///
/// path: 相对于 session 工作区的路径 (如 "main.tex" 或 "out/fig.pdf")
#[tauri::command]
fn open_in_file_manager(sid: String, path: String) -> Result<(), String> {
    // 用 ensure_data_dir() 保证和后端写文件的位置一致, 不依赖环境变量
    let data_dir = ensure_data_dir();
    let abs = data_dir.join("workspaces").join(&sid).join(&path);

    if !abs.exists() {
        return Err(format!("file not found: {}", abs.display()));
    }

    eprintln!("[axiom] open in file manager: {}", abs.display());

    #[cfg(target_os = "macos")]
    {
        // -R 会在 Finder 中选中该文件并打开所在目录, 比只打开目录更直观
        let status = std::process::Command::new("open")
            .arg("-R")
            .arg(&abs)
            .status()
            .map_err(|e| format!("failed to open Finder: {}", e))?;
        if !status.success() {
            return Err("open directory failed".to_string());
        }
    }
    #[cfg(target_os = "windows")]
    {
        // /select 会选中文件
        let status = std::process::Command::new("explorer")
            .arg(format!("/select,{}", abs.display()))
            .status()
            .map_err(|e| format!("failed to open Explorer: {}", e))?;
        if !status.success() {
            return Err("explorer open directory failed".to_string());
        }
    }
    #[cfg(target_os = "linux")]
    {
        let parent = abs.parent().unwrap_or_else(|| std::path::Path::new("."));
        let status = std::process::Command::new("xdg-open")
            .arg(parent)
            .status()
            .map_err(|e| format!("failed to open file manager: {}", e))?;
        if !status.success() {
            return Err("xdg-open failed".to_string());
        }
    }
    Ok(())
}

/// 在系统默认浏览器里打开外部 URL。
///
/// 为什么需要这个命令: Tauri webview (macOS WKWebView / Windows WebView2) 里
/// window.open(外部URL) 会静默失败 — 不打开浏览器, 也不报错。
/// 项目里所有"打开外部链接"的地方都应走这里, 而不是 window.open。
///
/// 协议白名单: 只允许 http/https, 防止恶意构造 file:// 或自定义协议。
#[tauri::command]
fn open_external_url(url: String) -> Result<(), String> {
    // 协议白名单 (大小写不敏感)
    let lower = url.to_lowercase();
    if !(lower.starts_with("http://") || lower.starts_with("https://")) {
        return Err(format!(
            "refused to open non-http(s) URL (only http/https allowed): {}",
            url
        ));
    }

    eprintln!("[axiom] open external url: {}", url);

    #[cfg(target_os = "macos")]
    let mut cmd = {
        let mut c = std::process::Command::new("open");
        c.arg(&url);
        c
    };
    #[cfg(target_os = "windows")]
    let mut cmd = {
        // start "" <url>: 空 title 避免 URL 被当成窗口标题 (尤其当 URL 含 & 等字符)
        let mut c = std::process::Command::new("cmd");
        c.arg("/C").arg("start").arg("").arg(&url);
        c
    };
    #[cfg(target_os = "linux")]
    let mut cmd = {
        let mut c = std::process::Command::new("xdg-open");
        c.arg(&url);
        c
    };

    cmd.status()
        .map_err(|e| format!("failed to open browser: {}", e))?;
    Ok(())
}

/// 向前端注入后端端口, 并导航到工作台 /app。
/// 用 eval 注入端口到 localStorage (跨重载持久), 然后跳转 /app。
fn inject_port_and_navigate(window: &WebviewWindow, port: u16) {
    let script = format!(
        "localStorage.setItem('axiom_backend_port', '{}'); window.__BACKEND_PORT__ = {}; if (window.location.pathname === '/' || !window.location.pathname.startsWith('/app')) {{ window.location.replace('/app'); }}",
        port, port
    );
    let _ = window.eval(&script);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let backend: Arc<Mutex<Option<Child>>> = Arc::new(Mutex::new(None));
    let backend_port: Arc<Mutex<u16>> = Arc::new(Mutex::new(find_free_port(17896)));

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(AppState {
            backend: backend.clone(),
            backend_port: backend_port.clone(),
        })
        .invoke_handler(tauri::generate_handler![
            restart_backend,
            check_update,
            open_in_file_manager,
            open_external_url
        ])
        .setup(move |app| {
            let window = app.get_webview_window("main").unwrap();

            // macOS: 红绿灯内移 (启动时排一次, 之后每次 resize 重排)
            #[cfg(target_os = "macos")]
            if let Ok(ptr) = window.ns_window() {
                inset_traffic_lights(ptr, TRAFFIC_LIGHT_X, TRAFFIC_LIGHT_Y);
            }
            // 启动后 AppKit 还会因 webview 导航/首次布局把按钮复位, 延迟补排几次
            #[cfg(target_os = "macos")]
            {
                let window_for_delay = window.clone();
                tauri::async_runtime::spawn(async move {
                    for delay_ms in [500u64, 1500, 4000] {
                        tokio::time::sleep(std::time::Duration::from_millis(delay_ms)).await;
                        if let Ok(ptr) = window_for_delay.ns_window() {
                            inset_traffic_lights(ptr, TRAFFIC_LIGHT_X, TRAFFIC_LIGHT_Y);
                        }
                    }
                });
            }

            // 启动后端; 前端自己会探测 /api/health, 所以这里不需要等健康检查。
            let backend_clone = backend.clone();
            let port = *backend_port.blocking_lock();
            let app_handle = app.handle().clone();
            let window_for_nav = window.clone();
            tauri::async_runtime::spawn(async move {
                match start_backend(&app_handle, port).await {
                    Ok(child) => {
                        let pid = child.id().unwrap_or(0);
                        eprintln!("[axiom] backend started pid={}", pid);
                        *backend_clone.lock().await = Some(child);
                    }
                    Err(e) => {
                        eprintln!("[axiom] failed to start backend: {}", e);
                    }
                }
                // 注入端口并导航到工作台
                inject_port_and_navigate(&window_for_nav, port);
                let _ = window_for_nav.show();
                let _ = window_for_nav.set_focus();
            });

            // 主窗口关闭时同步杀掉后端并退出应用 (避免后台占端口)。
            let backend_for_close = backend.clone();
            let app_handle_for_close = app.handle().clone();
            let window_for_resize = window.clone();
            window.on_window_event(move |event| {
                // resize 后 AppKit 会把红绿灯复位, 重新内移
                #[cfg(target_os = "macos")]
                if let tauri::WindowEvent::Resized(_) = event {
                    if let Ok(ptr) = window_for_resize.ns_window() {
                        inset_traffic_lights(ptr, TRAFFIC_LIGHT_X, TRAFFIC_LIGHT_Y);
                    }
                }
                if let tauri::WindowEvent::CloseRequested { .. } = event {
                    eprintln!("[axiom] main window closing, stopping backend...");
                    let backend_clone = backend_for_close.clone();
                    let app_handle_clone = app_handle_for_close.clone();
                    tauri::async_runtime::spawn(async move {
                        if let Some(mut child) = backend_clone.lock().await.take() {
                            kill_backend_tree(&mut child).await;
                        }
                        app_handle_clone.exit(0);
                    });
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(move |app, event| {
            if let RunEvent::Exit = event {
                let backend_clone = app.state::<AppState>().backend.clone();
                tauri::async_runtime::block_on(async move {
                    if let Some(mut child) = backend_clone.lock().await.take() {
                        kill_backend_tree(&mut child).await;
                        eprintln!("[axiom] backend stopped");
                    }
                });
            }
        });
}
