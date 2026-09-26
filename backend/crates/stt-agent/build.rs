//! Per-platform fix-ups for `sherpa-onnx-sys`'s prebuilt runtime libraries,
//! needed because the crate ships binaries but expects the *consumer* to
//! make them loadable.
//!
//! ## Windows: a DLL shadowing trap
//!
//! sherpa-onnx-sys copies its runtime DLLs (sherpa-onnx-c-api.dll plus its bundled
//! onnxruntime.dll) into `target/<profile>/`, but test executables run from
//! `target/<profile>/deps/`. Windows resolves DLLs by searching the executable's
//! own directory first, then **System32 — which comes before PATH** — and Windows 11
//! ships its own `onnxruntime.dll` (Windows ML, ORT 1.17) in System32. The result:
//! sherpa's C API, built against a newer ONNX Runtime, loads the OS's 1.17 runtime,
//! prints "The requested API version [27] is not available", and dies with an
//! access violation. `cargo test -p stt-agent` then fails on any stock Windows 11
//! machine even though every crate built cleanly.
//!
//! Staging the DLLs next to the test binaries (deps/) wins the search order over
//! System32.
//!
//! ## macOS: an invalid signature *and* a missing rpath
//!
//! This file used to claim Linux and macOS "resolve via the `$ORIGIN`/
//! `@loader_path` rpath that sherpa-onnx-sys already emits, so this is a no-op
//! there." That was asserted, never exercised (`cargo test` was not in CI --
//! see audit/ROADMAP.md P2-11/M5-T1), and it was wrong on two independent
//! counts, found by actually running the test binary:
//!
//! 1. `otool -l` on the test binary shows **no `LC_RPATH` entry at all** --
//!    there was nothing for `@loader_path` to resolve, on either the library
//!    or the binary that links it.
//! 2. Even with an rpath injected, the process still SIGKILLed. `codesign -v`
//!    on the staged `libonnxruntime.*.dylib` reports "invalid signature (code
//!    or signature have been modified)" -- the prebuilt archive's signature
//!    does not survive extraction, and arm64 macOS SIGKILLs any process that
//!    loads a dylib with an invalid signature. No output, no catchable
//!    signal, which is why three prior debugging attempts (audit/ISSUES.md
//!    M5-T2) all "still SIGKILLed" and the residual cause was filed as
//!    UNKNOWN.
//!
//! Both are fixed here: emit an rpath covering both the profile directory
//! (where sherpa-onnx-sys places its dylibs, and where the production binary
//! itself lives) and one level up from it (where test binaries in `deps/`
//! need to look), then ad-hoc re-sign every dylib the profile directory
//! holds. Ad-hoc signing (`codesign -s -`) is a local, unnotarized signature
//! -- sufficient to satisfy the SIGKILL-on-invalid-signature check, not a
//! substitute for a real one; nothing here ships outside this build.

use std::env;
use std::fs;
use std::path::PathBuf;
use std::process::Command;

fn main() {
    println!("cargo:rerun-if-changed=build.rs");

    match env::var("CARGO_CFG_TARGET_OS").as_deref() {
        Ok("windows") => stage_windows_dlls(),
        Ok("macos") => fix_macos_dylibs(),
        _ => {}
    }
}

/// OUT_DIR = <target>/<profile>/build/stt-agent-<hash>/out
fn profile_dir() -> Option<PathBuf> {
    let out_dir = PathBuf::from(env::var("OUT_DIR").expect("cargo always sets OUT_DIR"));
    out_dir.ancestors().nth(3).map(PathBuf::from)
}

fn stage_windows_dlls() {
    let Some(profile_dir) = profile_dir() else {
        return;
    };
    let deps_dir = profile_dir.join("deps");
    // sherpa-onnx-sys caches its extracted prebuilt archives under <target>/.
    let Some(prebuilt_root) = profile_dir.parent().map(|t| t.join("sherpa-onnx-prebuilt")) else {
        return;
    };

    // sherpa-onnx-sys (a dependency) has already run and downloaded the archive by
    // the time this script executes. If the layout ever changes this quietly does
    // nothing — and the loud System32 version-mismatch error comes back, which is
    // itself the signal to revisit this script.
    let Ok(archives) = fs::read_dir(&prebuilt_root) else {
        return;
    };
    for archive in archives.flatten() {
        let lib_dir = archive.path().join("lib");
        let Ok(entries) = fs::read_dir(&lib_dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) != Some("dll") {
                continue;
            }
            let Some(name) = path.file_name() else {
                continue;
            };
            if let Err(err) = fs::copy(&path, deps_dir.join(name)) {
                println!(
                    "cargo:warning=could not stage {} into deps/: {err}",
                    path.display()
                );
            }
        }
    }
}

fn fix_macos_dylibs() {
    let Some(profile_dir) = profile_dir() else {
        return;
    };

    // `@loader_path` covers a binary colocated with the dylibs (the
    // production `stt-agent` binary, built directly into `profile_dir`);
    // `@loader_path/..` covers a test binary one level down, in `deps/`.
    println!("cargo:rustc-link-arg=-Wl,-rpath,@loader_path");
    println!("cargo:rustc-link-arg=-Wl,-rpath,@loader_path/..");

    // sherpa-onnx-sys places its extracted `.dylib`s directly in the profile
    // directory (confirmed by inspection, not documented upstream) — unlike
    // the Windows path above, nothing needs staging, only re-signing.
    let Ok(entries) = fs::read_dir(&profile_dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.extension().and_then(|e| e.to_str()) != Some("dylib") {
            continue;
        }
        // Ad-hoc signing ("-s -") is a local, unnotarized signature — it
        // exists only to satisfy the OS's "does this dylib have *a* valid
        // signature" check, which the extracted archive's original signature
        // fails. `-f` re-signs in place; already-valid signatures are
        // harmless to redo, so this does not need to detect which dylibs
        // actually need it.
        let output = Command::new("codesign")
            .args(["-f", "-s", "-"])
            .arg(&path)
            .output();
        match output {
            Ok(out) if !out.status.success() => {
                println!(
                    "cargo:warning=codesign failed for {}: {}",
                    path.display(),
                    String::from_utf8_lossy(&out.stderr)
                );
            }
            Err(err) => {
                println!(
                    "cargo:warning=could not run codesign for {}: {err}",
                    path.display()
                );
            }
            _ => {}
        }
    }
}
