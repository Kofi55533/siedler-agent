import sys
from pathlib import Path
p = Path(sys.argv[1]); s = p.read_text()
def rep(old, new, count=1):
    global s
    assert s.count(old) >= count, old
    s = s.replace(old, new, count)
rep("use std::sync::Arc;\n", """use std::sync::Arc;
pub static PROF: [std::sync::atomic::AtomicU64; 6] = [const { std::sync::atomic::AtomicU64::new(0) }; 6];
fn prof<T>(i: usize, f: impl FnOnce() -> T) -> T {
    let t = std::time::Instant::now();
    let r = f();
    PROF[i].fetch_add(t.elapsed().as_nanos() as u64, std::sync::atomic::Ordering::Relaxed);
    r
}
""")
rep("    let before = match import(data, mach) {", "    let before = match prof(0, || import(data, mach)) {")
rep("    let reference = match import(data, mach) {", "    let reference = match prof(0, || import(data, mach)) {")
rep("    let before_key = key_of(&before);", "    let before_key = prof(1, || key_of(&before));")
rep("    mach.run_to(pc, ret);\n    let eax", "    prof(2, || mach.run_to(pc, ret));\n    let eax")
rep("            let mut diff = diff(&reference, &clean);\n            if values != expected",
    "            let mut diff = prof(3, || diff(&reference, &clean));\n            if values != expected")
rep("    let observed = Some(key_of(&reference) != before_key", "    let observed = Some(prof(4, || key_of(&reference)) != before_key")
rep("(call.port)(&mut clean)))", "prof(5, || (call.port)(&mut clean))))")
END = """        stop(&mut game.mach, GGL_TICK, &[ggl]);
    }
    report
}"""
i = s.rindex(END)
s = s[:i] + """        stop(&mut game.mach, GGL_TICK, &[ggl]);
    }
    let ms = |i: usize| PROF[i].load(std::sync::atomic::Ordering::Relaxed) / 1_000_000;
    eprintln!("PROFILE samples={} import_ms={} beforekey_ms={} native_ms={} diff_ms={} refkey_ms={} port_ms={}",
        report.groups.values().map(|g| g.samples.len()).sum::<usize>(), ms(0), ms(1), ms(2), ms(3), ms(4), ms(5));
    report
}""" + s[i + len(END):]
p.write_text(s)
