// Tauri 桌面壳二进制入口: 仅调用库中的 run()。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    axiom_lib::run();
}
