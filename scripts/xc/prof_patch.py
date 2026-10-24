import sys
from pathlib import Path
p = Path(sys.argv[1]); s = p.read_text()
def rep(old, new, count=1):
    global s
    assert s.count(old) >= count, old
    s = s.replace(old, new, count)
rep("use std::sync::Arc;\n", """use std::sync::Arc;
pub static PROF: [std::sync::atomic::AtomicU64; 6] = [const { std::sync::atomic::AtomicU64::new(0) }; 6];
pub static PART: [std::sync::atomic::AtomicU64; 16] = [const { std::sync::atomic::AtomicU64::new(0) }; 16];
fn prof<T>(i: usize, f: impl FnOnce() -> T) -> T {
    let t = std::time::Instant::now();
    let r = f();
    PROF[i].fetch_add(t.elapsed().as_nanos() as u64, std::sync::atomic::Ordering::Relaxed);
    r
}
""")
rep("    let before = match import(data, mach) {", "    let before = match prof(0, || import(data, mach)) {")
rep("    let reference = match import(data, mach) {", "    let reference = match prof(0, || import(data, mach)) {")
rep("    let before_key = key_of(&before);", """    let before_key = prof(1, || key_of(&before));
    {
        let mut part = |i: usize, f: &dyn Fn() -> usize| {
            let t = std::time::Instant::now();
            let n = f();
            PART[i].fetch_add(t.elapsed().as_nanos() as u64, std::sync::atomic::Ordering::Relaxed);
            PART[i + 8].fetch_add(n as u64, std::sync::atomic::Ordering::Relaxed);
        };
        part(0, &|| format!("{:?}", before.data).len());
        part(1, &|| format!("{:?}", before.terrain).len());
        part(2, &|| format!("{:?}", before.blocking).len());
        part(3, &|| format!("{:?}", before.entities).len());
        part(4, &|| format!("{:?}", before.ai).len());
        part(5, &|| format!("{:?}", before.ai_static).len());
        part(6, &|| format!("{:?}", before.ai_agent_search).len());
        part(7, &|| before_key.len());
    }""")
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
    let pm = |i: usize| PART[i].load(std::sync::atomic::Ordering::Relaxed) / 1_000_000;
    let pb = |i: usize| PART[i + 8].load(std::sync::atomic::Ordering::Relaxed) / 1_000_000;
    eprintln!("PARTS ms/MB data {}/{} terrain {}/{} blocking {}/{} entities {}/{} ai {}/{} ai_static {}/{} agent_search {}/{} whole -/{}",
        pm(0), pb(0), pm(1), pb(1), pm(2), pb(2), pm(3), pb(3), pm(4), pb(4), pm(5), pb(5), pm(6), pb(6), pb(7));
    report
}""" + s[i + len(END):]
p.write_text(s)
