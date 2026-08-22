// JARVIS desktop shell: lanza el backend Python (bridge web en 8765), espera a
// que responda, abre la ventana, y al salir apaga el backend con gracia.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::env;
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

const BASE: &str = "http://127.0.0.1:8765";
const FALLBACK_PYTHON: &str = r"H:\Python311\python.exe";
const FALLBACK_DIR: &str = r"C:\Users\Isaac\Desktop\PROYECTOS\JARVIS";

struct Backend(Mutex<Option<Child>>);
struct UiToken(String);

fn random_token() -> String {
    let mut bytes = [0u8; 32];
    #[cfg(windows)]
    {
        extern "system" {
            fn SystemFunction036(buffer: *mut u8, length: u32) -> u8;
        }
        unsafe {
            let _ = SystemFunction036(bytes.as_mut_ptr(), bytes.len() as u32);
        }
    }
    #[cfg(not(windows))]
    {
        use std::fs::File;
        use std::io::Read;
        if let Ok(mut f) = File::open("/dev/urandom") {
            let _ = f.read_exact(&mut bytes);
        }
    }
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn exe_dir() -> PathBuf {
    env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| env::current_dir().unwrap_or_else(|_| PathBuf::from(".")))
}

fn jarvis_dir() -> PathBuf {
    if let Ok(dir) = env::var("JARVIS_DIR") {
        let path = PathBuf::from(dir);
        if path.join("jarvis.py").is_file() {
            return path;
        }
    }
    let exe = exe_dir();
    let cargo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..");
    for candidate in [
        exe.clone(),
        exe.join(".."),
        exe.join("../.."),
        exe.join("../../.."),
        cargo_root,
        PathBuf::from(FALLBACK_DIR),
    ] {
        if let Ok(resolved) = candidate.canonicalize() {
            if resolved.join("jarvis.py").is_file() {
                return resolved;
            }
        }
    }
    PathBuf::from(FALLBACK_DIR)
}

fn python_exe(dir: &Path) -> PathBuf {
    if let Ok(py) = env::var("JARVIS_PYTHON") {
        let path = PathBuf::from(py);
        if path.is_file() {
            return path;
        }
    }
    let venv = dir.join(".venv").join("Scripts").join("python.exe");
    if venv.is_file() {
        return venv;
    }
    let fallback = PathBuf::from(FALLBACK_PYTHON);
    if fallback.is_file() {
        return fallback;
    }
    PathBuf::from("python")
}

fn spawn_python(dir: &Path, py: &Path, token: &str) -> std::io::Result<Child> {
    Command::new(py)
        .arg("jarvis.py")
        .current_dir(dir)
        .env("JARVIS_UI", "web")
        .env("JARVIS_WEB_UI_OPEN_BROWSER", "false")
        .env("JARVIS_WEB_UI_PORT", "8765")
        .env("JARVIS_SUPERVISED", "1")
        .env("JARVIS_UI_TOKEN", token)
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

fn graceful_shutdown(child: &mut Child, token: &str) {
    let _ = ureq::post(&format!("{BASE}/command"))
        .set("X-Jarvis-Ui-Token", token)
        .timeout(Duration::from_secs(2))
        .send_json(serde_json::json!({ "command": "close" }));
    for _ in 0..20 {
        if let Ok(Some(_)) = child.try_wait() {
            return;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    let _ = child.kill();
}

fn main() {
    let dir = jarvis_dir();
    let py = python_exe(&dir);
    let token = random_token();
    tauri::Builder::default()
        .setup(move |app| {
            let child = spawn_python(&dir, &py, &token)
                .map_err(|e| format!("no pude lanzar Python ({}): {e}", py.display()))?;
            app.manage(Backend(Mutex::new(Some(child))));
            app.manage(UiToken(token.clone()));
            let ready = wait_for_backend(90);
            if !ready {
                eprintln!(
                    "[desktop] backend no respondio en 90s; dir={} python={}",
                    dir.display(),
                    py.display()
                );
            }
            let start_url = format!("{BASE}/?access_token={token}");
            WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::External(start_url.parse().expect("url")),
            )
            .title(if ready {
                "JARVIS"
            } else {
                "JARVIS — backend no respondio"
            })
            .inner_size(1380.0, 860.0)
            .min_inner_size(1080.0, 700.0)
            .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error construyendo la app Tauri")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                let token = app.try_state::<UiToken>().map(|s| s.0.clone());
                if let Some(backend) = app.try_state::<Backend>() {
                    if let Some(mut child) = backend.0.lock().expect("lock").take() {
                        if let Some(token) = token {
                            graceful_shutdown(&mut child, &token);
                        } else {
                            let _ = child.kill();
                        }
                    }
                }
            }
        });
}
