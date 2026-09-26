"""Dedicated Jev game controller: ordinary scan-code and relative mouse input.

No game-memory writes. Only the explicitly selected L4D2 foreground process.
Short bounded actions, a single input owner, always release held controls.
"""
import ctypes as C
from ctypes import wintypes as W
import json
import msvcrt
import pathlib
import time
from obs_guard import ROOT, audit, client

U=C.WinDLL('user32',use_last_error=True)
K=C.WinDLL('kernel32',use_last_error=True)
class MI(C.Structure):
    _fields_=[('dx',W.LONG),('dy',W.LONG),('mouseData',W.DWORD),('dwFlags',W.DWORD),('time',W.DWORD),('extra',C.c_size_t)]
class KI(C.Structure):
    _fields_=[('vk',W.WORD),('scan',W.WORD),('flags',W.DWORD),('time',W.DWORD),('extra',C.c_size_t)]
class HI(C.Structure):
    _fields_=[('msg',W.DWORD),('l',W.WORD),('h',W.WORD)]
class IU(C.Union):
    _fields_=[('mi',MI),('ki',KI),('hi',HI)]
class INPUT(C.Structure):
    _anonymous_=('data',)
    _fields_=[('type',W.DWORD),('data',IU)]
U.SendInput.argtypes=[W.UINT,C.POINTER(INPUT),C.c_int]
U.SendInput.restype=W.UINT
U.GetForegroundWindow.restype=W.HWND
U.GetWindowThreadProcessId.argtypes=[W.HWND,C.POINTER(W.DWORD)]
U.GetClientRect.argtypes=[W.HWND,C.POINTER(W.RECT)]
U.GetClientRect.restype=W.BOOL
U.ClientToScreen.argtypes=[W.HWND,C.POINTER(W.POINT)]
U.ClientToScreen.restype=W.BOOL
U.GetClipCursor.argtypes=[C.POINTER(W.RECT)]
U.GetClipCursor.restype=W.BOOL
U.ClipCursor.argtypes=[C.POINTER(W.RECT)]
U.ClipCursor.restype=W.BOOL
K.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD]
K.OpenProcess.restype=W.HANDLE
K.QueryFullProcessImageNameW.argtypes=[W.HANDLE,W.DWORD,W.LPWSTR,C.POINTER(W.DWORD)]
K.CloseHandle.argtypes=[W.HANDLE]
K.GetExitCodeProcess.argtypes=[W.HANDLE,C.POINTER(W.DWORD)]
K.GetExitCodeProcess.restype=W.BOOL
SCANS={'escape':1,'1':2,'2':3,'3':4,'4':5,'5':6,'w':17,'e':18,'r':19,
       'enter':28,'ctrl':29,'a':30,'s':31,'d':32,'f':33,'shift':42,
       'space':57,'f9':67,'f10':68,'f11':87}
BUTTONS={'fire':(2,4),'shove':(8,16)}

def foreground_pid():
    p=W.DWORD(); U.GetWindowThreadProcessId(U.GetForegroundWindow(),C.byref(p)); return p.value

def cursor_clip():
    rect=W.RECT()
    if not U.GetClipCursor(C.byref(rect)):raise C.WinError(C.get_last_error())
    return (rect.left,rect.top,rect.right,rect.bottom)

def set_cursor_clip(bounds):
    rect=W.RECT(*bounds)
    if not U.ClipCursor(C.byref(rect)):raise C.WinError(C.get_last_error())

def game_cursor_bounds(pid):
    hwnd=U.GetForegroundWindow(); owner=W.DWORD()
    U.GetWindowThreadProcessId(hwnd,C.byref(owner))
    if owner.value!=pid:raise RuntimeError('Game lost foreground ownership')
    rect=W.RECT()
    if not U.GetClientRect(hwnd,C.byref(rect)):raise C.WinError(C.get_last_error())
    origin=W.POINT(rect.left,rect.top);corner=W.POINT(rect.right,rect.bottom)
    if not U.ClientToScreen(hwnd,C.byref(origin)) or not U.ClientToScreen(hwnd,C.byref(corner)):
        raise C.WinError(C.get_last_error())
    if corner.x-origin.x<4 or corner.y-origin.y<4:raise RuntimeError('Game client has no usable cursor bounds')
    if U.GetForegroundWindow()!=hwnd:raise RuntimeError('Game lost foreground ownership')
    return (origin.x+1,origin.y+1,corner.x-1,corner.y-1)

class CursorBoundary:
    """Own a temporary game-client cursor boundary, never another app's."""
    def __init__(self,pid):
        self.pid=pid;self.original=None;self.owned=None
    def ensure(self):
        bounds=game_cursor_bounds(self.pid);current=cursor_clip()
        if self.original is None:self.original=current
        if current!=bounds:
            set_cursor_clip(bounds)
            self.owned=bounds
        elif self.owned is not None:self.owned=bounds
        if foreground_pid()!=self.pid:
            self.release()
            raise RuntimeError('Game lost foreground ownership')
    def release(self):
        if self.owned is not None:
            if cursor_clip()==self.owned:set_cursor_clip(self.original)
        self.original=None;self.owned=None

def game_process_exited(pid):
    """Require process-exit evidence; focus loss alone is insufficient."""
    h=K.OpenProcess(0x1000,False,pid)
    if not h:return C.get_last_error()==87
    try:
        code=W.DWORD()
        if not K.GetExitCodeProcess(h,C.byref(code)):return False
        return code.value!=259  # STILL_ACTIVE
    finally:K.CloseHandle(h)

def send(event):
    if U.SendInput(1,C.byref(event),C.sizeof(INPUT))!=1: raise C.WinError(C.get_last_error())

def key(name,up=False):
    return INPUT(type=1,ki=KI(0,SCANS[name],8|(2 if up else 0),0,0))

def mouse(flags,dx=0,dy=0):
    return INPUT(type=0,mi=MI(dx,dy,0,flags,0,0))

class GameInput:
    def __init__(self,pid):
        h=K.OpenProcess(0x1000,False,pid)
        if not h: raise C.WinError(C.get_last_error())
        try:
            path=C.create_unicode_buffer(32768); length=W.DWORD(len(path))
            if not K.QueryFullProcessImageNameW(h,0,path,C.byref(length)): raise C.WinError(C.get_last_error())
            expected=pathlib.Path(r'C:\Program Files (x86)\Steam\steamapps\common\Left 4 Dead 2\left4dead2.exe')
            if pathlib.Path(path.value)!=expected: raise RuntimeError('Input target is not the verified game')
        finally: K.CloseHandle(h)
        self.pid=pid; self.obs=client(); self.last_audit=0; self.previous=None
        self.held_keys=set();self.held_buttons=set()
        self.lock=(ROOT/'controller.lock').open('a+b'); self.lock.seek(0)
        if not self.lock.read(1): self.lock.write(b'0'); self.lock.flush()
        self.lock.seek(0); msvcrt.locking(self.lock.fileno(),msvcrt.LK_NBLCK,1)
        self.cursor_boundary=CursorBoundary(pid)
    def release_cursor(self):
        boundary=getattr(self,'cursor_boundary',None)
        if boundary is not None:boundary.release()
    def guard(self):
        try:
            if foreground_pid()!=self.pid: raise RuntimeError('Game lost foreground ownership')
            if (ROOT/'controller.stop').exists(): raise RuntimeError('Controller stop requested')
            now=time.monotonic()
            if now-self.last_audit>.5:
                current=audit(self.obs,require_recording=True)['recording']
                if self.previous and now-self.previous[0]>1.5:
                    if current['outputDuration']<=self.previous[1]: raise RuntimeError('Recorder timer stopped advancing')
                    self.previous=(now,current['outputDuration'])
                elif not self.previous: self.previous=(now,current['outputDuration'])
                self.last_audit=now
            self.cursor_boundary.ensure()
        except BaseException:
            self.release_cursor()
            raise
    def act(self,keys=(),buttons=(),dx=0,dy=0,seconds=.15):
        if not .05<=seconds<=1 or len(keys)>4 or len(buttons)>2: raise ValueError('Unbounded action')
        if any(k not in SCANS for k in keys) or any(b not in BUTTONS for b in buttons): raise ValueError('Input outside allowlist')
        if any(not isinstance(v,int) or abs(v)>1200 for v in (dx,dy)): raise ValueError('Mouse delta outside bounds')
        self.guard(); held=[]; clicked=[]
        try:
            if dx or dy: send(mouse(1,dx,dy))
            for k in keys: send(key(k)); held.append(k)
            for b in buttons: send(mouse(BUTTONS[b][0])); clicked.append(b)
            end=time.monotonic()+seconds
            while time.monotonic()<end:
                time.sleep(min(.025,max(0,end-time.monotonic()))); self.guard()
        except BaseException:
            self.release_cursor()
            raise
        finally:
            for b in reversed(clicked): send(mouse(BUTTONS[b][1]))
            for k in reversed(held): send(key(k,True))
    def close(self):
        try:
            if foreground_pid()==self.pid: self.release_all()
            else:self.release_held()
        finally:
            try:self.release_cursor()
            finally:
                self.lock.seek(0); msvcrt.locking(self.lock.fileno(),msvcrt.LK_UNLCK,1); self.lock.close()
    def release_held(self):
        # Focus loss stops new game actions, but our prior held keys/buttons
        # still need key-up events. Release only inputs this controller owns;
        # never issue new presses or sweep unrelated keys in another app.
        for name in self.held_keys:send(key(name,True))
        for name in self.held_buttons:send(mouse(BUTTONS[name][1]))
        self.held_keys.clear();self.held_buttons.clear()
    def release_all(self):
        if foreground_pid()!=self.pid: raise RuntimeError('Cannot release inputs outside the selected game')
        for name in SCANS: send(key(name,True))
        for pair in BUTTONS.values(): send(mouse(pair[1]))
        self.held_keys.clear();self.held_buttons.clear()
    def sustain(self,keys=(),buttons=(),dx=0,dy=0,seconds=.08,pulse_movement=False):
        """One bounded feedback tick, retaining only explicitly requested controls."""
        keys=set(keys);buttons=set(buttons)
        if not .05<=seconds<=.15 or len(keys)>4 or len(buttons)>2:raise ValueError('Unbounded continuous action')
        if not keys<=SCANS.keys() or not buttons<=BUTTONS.keys():raise ValueError('Input outside allowlist')
        if any(not isinstance(v,int) or abs(v)>1200 for v in (dx,dy)):raise ValueError('Unbounded look')
        try:
            self.guard()
            for name in self.held_keys-keys:send(key(name,True))
            for name in self.held_buttons-buttons:send(mouse(BUTTONS[name][1]))
            if dx or dy:send(mouse(1,dx,dy))
            for name in keys-self.held_keys:send(key(name))
            for name in buttons-self.held_buttons:send(mouse(BUTTONS[name][0]))
            self.held_keys=keys;self.held_buttons=buttons
            end=time.monotonic()+seconds
            while time.monotonic()<end:
                time.sleep(min(.025,max(0,end-time.monotonic())));self.guard()
            if pulse_movement:
                # Route calculations can outlast a feedback tick. On narrow
                # ground, do not keep walking while the next route is computed.
                for name in self.held_keys&{'w','a','s','d','space'}:
                    send(key(name,True))
                self.held_keys-= {'w','a','s','d','space'}
        except BaseException:
            try:self.release_held()
            finally:self.release_cursor()
            raise
    def hold_fire(self,seconds,keep_holding):
        """Continuous medkit use, with the same focus/capture checks and release."""
        if not .05<=seconds<=7: raise ValueError('Unbounded hold')
        self.guard()
        try:
            send(mouse(BUTTONS['fire'][0]));end=time.monotonic()+seconds
            while time.monotonic()<end:
                self.guard()
                if not keep_holding(): break
                time.sleep(.05)
        except BaseException:
            self.release_cursor()
            raise
        finally: send(mouse(BUTTONS['fire'][1]))
    def emergency_pause(self):
        """Pause an advancing game and return whether that input froze its clock.

        An already stale observation is not proof of a pause. Callers must not
        stop recording on that evidence alone.
        """
        from observe_game import EMS,read_json
        a=read_json(EMS/'state.json');time.sleep(.2);b=read_json(EMS/'state.json')
        if a['seq']==b['seq']: return False
        if foreground_pid()!=self.pid: raise RuntimeError('Cannot pause while game lacks focus')
        try: send(key('f9'));time.sleep(.15)
        finally: send(key('f9',True))
        time.sleep(.15);c=read_json(EMS/'state.json')
        time.sleep(.25);d=read_json(EMS/'state.json')
        return (c['seq']==d['seq'] and c['t']==d['t']
            and c['seq']>=b['seq'] and c['t']>=b['t']
            and b['map']==c['map']==d['map']
            and b.get('round_id')==c.get('round_id')==d.get('round_id'))

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('--pid',type=int,required=True)
    p.add_argument('--key',choices=SCANS,required=True)
    a=p.parse_args(); control=GameInput(a.pid)
    try: control.act(keys=[a.key])
    finally: control.close()
