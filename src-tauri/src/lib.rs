// Tauri 桌面壳库入口。
// 启动时自动拉起 Python 后端, 注入后端端口, 关闭时清理进程。
// macOS 上启用系统级毛玻璃(vibrancy)效果。

use std::fs;
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use tauri::{Manager, RunEvent, WebviewWindow};
use tokio::process::{Child, Command};
use tokio::sync::Mutex;



struct AppState {
    backend: Arc<Mutex<Option<Child>>>,
    backend_port: Arc<Mutex<u16>>,
}

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
    cmd.args(&args)
        .current_dir(&work_dir)
        .env("OPERON_DATA_DIR", {
            let dir = ensure_data_dir();
            eprintln!("[axiom] OPERON_DATA_DIR = {:?}", dir);
            dir
        })
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .kill_on_drop(true);

    eprintln!("[axiom] starting backend: {:?} {:?} (cwd={:?})", bin, args, work_dir);

    let mut child = cmd.spawn()?;

    Ok(child)
}

#[tauri::command]
async fn restart_backend(state: tauri::State<'_, AppState>, app_handle: tauri::AppHandle) -> Result<String, String> {
    let mut guard = state.backend.lock().await;
    if let Some(mut child) = guard.take() {
        let _ = child.kill().await;
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
        .invoke_handler(tauri::generate_handler![restart_backend])
        .setup(move |app| {
            let window = app.get_webview_window("main").unwrap();

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
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(move |app, event| {
            if let RunEvent::Exit = event {
                let backend_clone = app.state::<AppState>().backend.clone();
                tauri::async_runtime::block_on(async move {
                    if let Some(mut child) = backend_clone.lock().await.take() {
                        let _ = child.kill().await;
                        eprintln!("[axiom] backend stopped");
                    }
                });
            }
        });
}
