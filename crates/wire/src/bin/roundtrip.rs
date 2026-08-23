//! Emits the Rust side of the AC-30′ cross-language contract test.
//!
//! pytest cannot invoke `cargo test`, so the Rust round-trip result is materialized as a file
//! the Python test reads. `make wire-roundtrip` runs this before pytest.
//!
//! Usage: `cargo run -p wire --bin roundtrip [fixture_name ...]`
//! With no arguments, processes every fixture in `tests/fixtures/openrtb/`.

use std::path::{Path, PathBuf};

fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures/openrtb")
}

fn roundtrip(path: &Path) -> Result<serde_json::Value, String> {
    let raw = std::fs::read_to_string(path).map_err(|e| format!("read: {e}"))?;
    let name = path.file_stem().unwrap_or_default().to_string_lossy().to_string();

    // Responses and requests are different types; pick by shape rather than by filename.
    let probe: serde_json::Value =
        serde_json::from_str(&raw).map_err(|e| format!("{name}: not JSON: {e}"))?;

    if probe.get("seatbid").is_some() {
        let v: wire::BidResponse =
            serde_json::from_str(&raw).map_err(|e| format!("{name}: BidResponse: {e}"))?;
        serde_json::to_value(&v).map_err(|e| format!("{name}: reserialize: {e}"))
    } else {
        let v: wire::BidRequest =
            serde_json::from_str(&raw).map_err(|e| format!("{name}: BidRequest: {e}"))?;
        serde_json::to_value(&v).map_err(|e| format!("{name}: reserialize: {e}"))
    }
}

fn main() -> std::process::ExitCode {
    let dir = fixtures_dir();
    let out_dir = dir.join(".rust_out");
    if let Err(e) = std::fs::create_dir_all(&out_dir) {
        eprintln!("cannot create {}: {e}", out_dir.display());
        return std::process::ExitCode::FAILURE;
    }

    let args: Vec<String> = std::env::args().skip(1).collect();
    let names: Vec<String> = if args.is_empty() {
        match std::fs::read_dir(&dir) {
            Ok(rd) => rd
                .filter_map(|e| e.ok())
                .map(|e| e.path())
                .filter(|p| p.extension().is_some_and(|x| x == "json"))
                .filter_map(|p| p.file_stem().map(|s| s.to_string_lossy().to_string()))
                .collect(),
            Err(e) => {
                eprintln!("cannot read {}: {e}", dir.display());
                return std::process::ExitCode::FAILURE;
            }
        }
    } else {
        args
    };

    let mut failed = false;
    for name in names {
        let src = dir.join(format!("{name}.json"));
        match roundtrip(&src) {
            Ok(v) => {
                let dst = out_dir.join(format!("{name}.json"));
                let text = serde_json::to_string_pretty(&v).unwrap();
                if let Err(e) = std::fs::write(&dst, text) {
                    eprintln!("write {}: {e}", dst.display());
                    failed = true;
                } else {
                    println!("ok {name}");
                }
            }
            Err(e) => {
                eprintln!("FAIL {e}");
                failed = true;
            }
        }
    }

    if failed {
        std::process::ExitCode::FAILURE
    } else {
        std::process::ExitCode::SUCCESS
    }
}
