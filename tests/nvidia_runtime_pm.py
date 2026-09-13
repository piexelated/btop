#!/usr/bin/env python3
"""Exercise the built btop against mock NVML and redirected PCI power status.

No real NVIDIA API is called. Requires a NVIDIA PCI function for sysfs discovery.
Run: python3 tests/nvidia_runtime_pm.py
"""
import fcntl
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import tempfile
import termios
import time

ROOT = Path(__file__).resolve().parents[1]
MOCK = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
static void record(const char *what) {
    FILE *f = fopen(getenv("MOCK_NVML_LOG"), "a");
    if (f) { fprintf(f,"%s\n",what); fclose(f); }
}

static const char *redirect(const char *path) {
    if (strstr(path,"/power/runtime_status") && strstr(path,"/sys/"))
        return getenv("MOCK_PM_STATUS");
    return path;
}
FILE *fopen(const char *path, const char *mode) {
    static FILE *(*real_open)(const char *,const char *);
    if (!real_open) real_open = dlsym(RTLD_NEXT,"fopen");
    return real_open(redirect(path),mode);
}
FILE *fopen64(const char *path, const char *mode) {
    static FILE *(*real_open)(const char *,const char *);
    if (!real_open) real_open = dlsym(RTLD_NEXT,"fopen64");
    return real_open(redirect(path),mode);
}
#include <stdarg.h>
int open(const char *path, int flags, ...) {
    static int (*real_open)(const char *,int,...);
    if (!real_open) real_open = dlsym(RTLD_NEXT,"open");
    int mode=0;
    if (flags & O_CREAT) { va_list ap; va_start(ap,flags); mode=va_arg(ap,int); va_end(ap); }
    return real_open(redirect(path),flags,mode);
}
int open64(const char *path, int flags, ...) {
    static int (*real_open)(const char *,int,...);
    if (!real_open) real_open = dlsym(RTLD_NEXT,"open64");
    int mode=0;
    if (flags & O_CREAT) { va_list ap; va_start(ap,flags); mode=va_arg(ap,int); va_end(ap); }
    return real_open(redirect(path),flags,mode);
}
const char *nvmlErrorString(int e) { return "mock error"; }
int nvmlInit(void) { record("init"); return 0; }
int nvmlShutdown(void) { record("shutdown"); return 0; }
int nvmlDeviceGetCount(unsigned *v) { *v=1; return 0; }
int nvmlDeviceGetHandleByPciBusId_v2(const char *id,void **v) { record("handle"); *v=(void*)1; return 0; }
int nvmlDeviceGetName(void *d,char *s,unsigned n) { snprintf(s,n,"NVIDIA Mock GPU"); return 0; }
int nvmlDeviceGetPowerManagementLimit(void *d,unsigned *v) { *v=35000; return 0; }
int nvmlDeviceGetTemperatureThreshold(void *d,int t,unsigned *v) { *v=100; return 0; }
int nvmlDeviceGetUtilizationRates(void *d,unsigned *v) { record("sample"); v[0]=20; v[1]=10; return 0; }
int nvmlDeviceGetClockInfo(void *d,int t,unsigned *v) { *v=300; return 0; }
int nvmlDeviceGetPowerUsage(void *d,unsigned *v) { *v=7000; return 0; }
int nvmlDeviceGetPowerState(void *d,int *v) { *v=8; return 0; }
int nvmlDeviceGetTemperature(void *d,int t,unsigned *v) { *v=40; return 0; }
int nvmlDeviceGetMemoryInfo(void *d,unsigned long long *v) { v[0]=1024*1024*1024; v[1]=v[0]/2; v[2]=v[0]/2; return 0; }
int nvmlDeviceGetPcieThroughput(void *d,int t,unsigned *v) { *v=0; return 0; }
int nvmlDeviceGetEncoderUtilization(void *d,unsigned *v,unsigned *p) { *v=0; *p=100; return 0; }
int nvmlDeviceGetDecoderUtilization(void *d,unsigned *v,unsigned *p) { *v=0; *p=100; return 0; }
'''


def main():
    assert any(p.read_text().strip() == '0x10de' for p in Path('/sys/bus/pci/devices').glob('*/vendor')), 'Test needs NVIDIA PCI discovery'
    with tempfile.TemporaryDirectory(prefix='btop-nvml-test-') as temp:
        root = Path(temp)
        (root / 'mock.c').write_text(MOCK)
        lib = root / 'libnvidia-ml.so'
        subprocess.run(['cc', '-shared', '-fPIC', '-o', str(lib), str(root / 'mock.c'), '-ldl'], check=True)
        (root / 'libnvidia-ml.so.1').symlink_to(lib.name)
        status = root / 'runtime_status'
        status.write_text('suspended\n')
        audit = root / 'nvml.log'
        conf = root / 'config/btop'
        conf.mkdir(parents=True)
        (conf / 'btop.conf').write_text('shown_boxes = "cpu gpu0 mem net proc"\nshown_gpus = "nvidia"\nupdate_ms = 200\nshow_gpu_info = "On"\n')
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 55, 160, 0, 0))
        env = dict(os.environ, TERM='xterm-256color', XDG_CONFIG_HOME=str(root/'config'),
                   XDG_STATE_HOME=str(root/'state'), LD_LIBRARY_PATH=str(root), LD_PRELOAD=str(lib),
                   MOCK_PM_STATUS=str(status), MOCK_NVML_LOG=str(audit))
        app = subprocess.Popen([str(ROOT/'bin/btop')], stdin=slave, stdout=slave, stderr=slave, env=env)
        os.close(slave)
        output = bytearray()
        def drain(seconds):
            end = time.monotonic()+seconds
            while time.monotonic() < end:
                if select.select([master], [], [], .05)[0]:
                    try: output.extend(os.read(master, 65536))
                    except OSError: break
            assert app.poll() is None, f'btop exited: {app.returncode}\n{output[-2000:]}'
        def calls():
            return audit.read_text().splitlines() if audit.exists() else []
        try:
            drain(2)
            assert calls() == [], calls()
            assert b'Sleeping' in output, output[-2000:]
            print('PASS: suspended startup displays Sleeping without any NVML call')
            for cycle in range(2):
                status.write_text('active\n')
                drain(2)
                events = calls()
                assert 'sample' in events and events.count('init') == events.count('shutdown'), events
                print(f'PASS: active cycle {cycle+1} samples and releases every NVML attachment')
                status.write_text('suspended\n')
                drain(.5)
                before = calls()
                output.clear()
                drain(1)
                assert calls() == before, calls()
                assert b'Sleeping' in output
                print('PASS: suspended polling makes no further NVML calls')
            status.write_text('suspending\n')
            before = calls()
            output.clear()
            drain(1)
            assert calls() == before and b'Monitoring paused' in output
            status.unlink()
            drain(1)
            assert calls() == before
            print('PASS: transitional and missing power status fail closed')
            os.write(master, b'q')
            app.wait(timeout=5)
            assert app.returncode == 0
        finally:
            if app.poll() is None:
                app.terminate()
                try: app.wait(timeout=3)
                except subprocess.TimeoutExpired: app.kill(); app.wait()
            os.close(master)


if __name__ == '__main__':
    main()
