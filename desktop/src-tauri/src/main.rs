// JARVIS desktop shell: lanza el backend Python (bridge web en 8765), espera a
// que responda, abre la ventana, y al salir apaga el backend con gracia.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

const BASE: &str = "http://127.0.0.1:8765";
const PYTHON: &str = "H:\\Python311\\python.exe";
const JARVIS_DIR: &str = "C:\\Users\\Isaac\\Desktop\\PROYECTOS\\JARVIS";

struct Backend(Mutex<Option<Child>>);

fn spawn_python() -> std::io::Result<Child> {
    Command::new(PYTHON)
        .arg("jarvis.py")
        .current_dir(JARVIS_DIR)
        .env("JARVIS_UI", "web")
        .env("JARVIS_WEB_UI_OPEN_BROWSER", "false")
        .env("JARVIS_WEB_UI_PORT", "8765")
        .env("JARVIS_SUPERVISED", "1")
        .env("PYTHONUTF8", "1")
        .spawn()
}

fn wait_for_backend(timeout_s: u64) -> bool {
    let deadline = Instant::now() + Duration::from_secs(timeout_s);
    let addr = "127.0.0.1:8765".parse().expect("addr");
    while Instant::now() < deadline {
        if TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    false
}

fn graceful_shutdown(child: &mut Child) {
    // El POST /command requiere el uiToken; /state lo expone (solo localhost).
    let token = ureq::get(&format!("{BASE}/state"))
        .timeout(Duration::from_secs(2))
        .call()
        .ok()
        .and_then(|r| r.into_json::<serde_json::Value>().ok())
        .and_then(|v| v["uiToken"].as_str().map(String::from));
    if let Some(token) = token {
        let _ = ureq::post(&format!("{BASE}/command"))
            .set("X-Jarvis-Ui-Token", &token)
            .timeout(Duration::from_secs(2))
            .send_json(serde_json::json!({ "command": "close" }));
    }
    // Hasta 5s de gracia para que JARVIS persista memoria/telemetria.
    for _ in 0..20 {
        if let Ok(Some(_)) = child.try_wait() {
            return;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    let _ = child.kill();
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let child = spawn_python().map_err(|e| format!("no pude lanzar Python: {e}"))?;
            app.manage(Backend(Mutex::new(Some(child))));
            if !wait_for_backend(45) {
                eprintln!("[desktop] backend no respondio en 45s; la ventana mostrara error de conexion");
            }
            WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::External(BASE.parse().expect("url")),
            )
            .title("JARVIS")
            .inner_size(1380.0, 860.0)
            .min_inner_size(1080.0, 700.0)
            .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error construyendo la app Tauri")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(backend) = app.try_state::<Backend>() {
                    if let Some(mut child) = backend.0.lock().expect("lock").take() {
                        graceful_shutdown(&mut child);
                    }
                }
            }
        });
}
