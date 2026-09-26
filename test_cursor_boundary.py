"""Prevent game turns/clicks escaping a window; restore cursor at handoff."""
import unittest
from unittest.mock import Mock,patch
import game_input as g


class CursorBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.desktop=(-1920,0,1920,1080);self.game=(321,181,1599,899)
        self.clip=self.desktop;self.pid=42
        self.boundary=g.CursorBoundary(self.pid)
        def set_clip(value):self.clip=value
        for target,kwargs in (
            ('cursor_clip',{'side_effect':lambda:self.clip}),
            ('set_cursor_clip',{'side_effect':set_clip}),
            ('game_cursor_bounds',{'return_value':self.game}),
            ('foreground_pid',{'side_effect':lambda:self.pid}),
        ):
            p=patch.object(g,target,**kwargs);p.start();self.addCleanup(p.stop)

    def test_large_turn_then_trigger_stays_inside_client_and_restores_desktop(self):
        control=g.GameInput.__new__(g.GameInput)
        control.pid=42;control.held_keys=set();control.held_buttons=set()
        control.cursor_boundary=self.boundary;control.guard=self.boundary.ensure
        cursor=[960,540];clicks=[]
        def send(event):
            if event.type!=0:return
            if event.mi.dwFlags==1:
                cursor[0]=max(self.clip[0],min(self.clip[2]-1,cursor[0]+event.mi.dx))
                cursor[1]=max(self.clip[1],min(self.clip[3]-1,cursor[1]+event.mi.dy))
            elif event.mi.dwFlags==2:clicks.append(tuple(cursor))
        with patch.object(g,'send',side_effect=send):
            control.sustain(dx=-1200,dy=1000,buttons=['fire'],seconds=.05)
            control.release_held()
        self.assertEqual(clicks,[(321,898)])
        self.assertEqual(self.clip,self.game)
        control.release_cursor();self.assertEqual(self.clip,self.desktop)

    def test_reassert_after_game_unclips_and_restore_original_once(self):
        self.boundary.ensure();self.clip=self.desktop;self.boundary.ensure()
        self.assertEqual(self.clip,self.game)
        self.boundary.release();self.boundary.release()
        self.assertEqual(self.clip,self.desktop)

    def test_precision_tick_stops_walking_between_updates_without_breaking_support_or_fire(self):
        control=g.GameInput.__new__(g.GameInput)
        control.pid=42;control.held_keys=set();control.held_buttons=set()
        control.cursor_boundary=self.boundary;control.guard=self.boundary.ensure
        sent=[]
        with patch.object(g,'send',side_effect=sent.append):
            control.sustain(keys=['w','e','shift'],buttons=['fire'],seconds=.05,pulse_movement=True)
            self.assertEqual(control.held_keys,{'e','shift'})
            self.assertEqual(control.held_buttons,{'fire'})
            ups=[e.ki.scan for e in sent if e.type==1 and e.ki.flags&2]
            self.assertEqual(ups,[g.SCANS['w']])
            control.release_held()


    def test_different_application_boundary_is_not_overwritten_on_release(self):
        self.boundary.ensure();self.clip=(10,10,200,200)
        self.boundary.release();self.assertEqual(self.clip,(10,10,200,200))

    def test_focus_change_during_acquisition_restores_desktop(self):
        self.pid=99
        with self.assertRaisesRegex(RuntimeError,'foreground'):self.boundary.ensure()
        self.assertEqual(self.clip,self.desktop)

    def test_focus_or_capture_guard_failure_releases_boundary_without_new_input(self):
        for failure in ('focus','capture'):
            with self.subTest(failure=failure):
                self.pid=42;self.boundary.ensure()
                control=g.GameInput.__new__(g.GameInput)
                control.pid=42;control.cursor_boundary=self.boundary
                control.obs=Mock();control.last_audit=0;control.previous=None
                if failure=='focus':self.pid=99
                with patch.object(g,'audit',side_effect=RuntimeError('capture failed')), \
                     patch.object(g,'send') as send,patch.object(g,'ROOT') as root:
                    root.__truediv__.return_value.exists.return_value=False
                    with self.assertRaises(RuntimeError):control.guard()
                    send.assert_not_called()
                self.assertEqual(self.clip,self.desktop)

    def test_send_failure_in_bounded_action_releases_cursor(self):
        control=g.GameInput.__new__(g.GameInput)
        control.cursor_boundary=self.boundary;control.guard=self.boundary.ensure
        with patch.object(g,'send',side_effect=OSError('input failed')):
            with self.assertRaises(OSError):control.act(dx=1,seconds=.05)
        self.assertEqual(self.clip,self.desktop)

    def test_close_releases_cursor_and_lock_even_if_input_cleanup_fails(self):
        control=g.GameInput.__new__(g.GameInput)
        control.pid=42;control.cursor_boundary=self.boundary;control.lock=Mock()
        control.release_all=Mock(side_effect=OSError('release failed'))
        self.boundary.ensure()
        with patch.object(g.msvcrt,'locking') as unlock:
            with self.assertRaises(OSError):control.close()
            unlock.assert_called_once()
        self.assertEqual(self.clip,self.desktop);control.lock.close.assert_called_once()

    def test_invalid_window_bounds_fail_before_confinement(self):
        with patch.object(g,'game_cursor_bounds',side_effect=RuntimeError('no usable bounds')):
            with self.assertRaisesRegex(RuntimeError,'no usable'):self.boundary.ensure()
        self.assertEqual(self.clip,self.desktop);self.assertIsNone(self.boundary.owned)


if __name__=='__main__':unittest.main(verbosity=2)
