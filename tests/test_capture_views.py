"""Exercise job resumption and native section discovery without Rhino's event loop."""
import os
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from test_settings import functions


class CaptureViews(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="enscape-views-")
        self.addCleanup(self.folder.cleanup)
        self.views = [("axon", os.path.join(self.folder.name, "axon.png")),
                      ("section", os.path.join(self.folder.name, "section.png"))]
        self.viewport = NS(SetViewProjection=Mock(return_value=True))
        self.view = NS(ActiveViewport=self.viewport, Redraw=Mock())
        self.doc = NS(RuntimeSerialNumber=42, Views=NS(ActiveView=self.view))
        self.scope = functions("capture_views", "_advance_views", "_finish_views", "_views_result", "VIEWS_KEY")
        self.scope.update(os=os, sc=NS(sticky={}, doc=self.doc), KEY="renderer",
                          Rhino=NS(DocObjects=NS(ViewportInfo=lambda vp: "original camera")),
                          status=lambda: dict(running=True, start_task="RanToCompletion"),
                          prepare_capture=Mock(), preparation_status=Mock(return_value={"preparation": "RanToCompletion"}),
                          request_capture=Mock(), collect_capture=self.collect)
        self.collected = []

    def collect(self):
        path = self.views[len(self.collected)][1]
        self.collected.append(path)
        return dict(capture_task="RanToCompletion", output=path, pixels=[800, 600],
                    files=[dict(output=path, pixels=[800, 600])])

    def poll(self, views=None):
        return self.scope["capture_views"](self.views if views is None else views)

    def finish(self):
        for _ in range(20):
            result = self.poll()
            if result["complete"]:
                return result
        self.fail("Job did not finish")

    def test_order_and_completed_calls_do_not_repeat_outputs(self):
        result = self.finish()
        self.assertTrue(result["passed"])
        self.assertEqual([item["state"] for item in result["views"]], ["complete", "complete"])
        self.assertEqual(self.collected, [path for _, path in self.views])
        self.assertEqual([call.args[0] for call in self.scope["prepare_capture"].call_args_list], ["axon", "section"])
        self.assertEqual(self.poll(), result)
        self.viewport.SetViewProjection.assert_called_once_with("original camera", True)

    def test_waiting_preparation_yields_without_requesting_capture(self):
        self.scope["preparation_status"].return_value = {"preparation": "WaitingForActivation"}
        self.poll()
        result = self.poll()
        self.assertFalse(result["complete"])
        self.scope["request_capture"].assert_not_called()
        self.viewport.SetViewProjection.assert_not_called()

    def test_changed_arguments_are_rejected_without_discarding_job(self):
        self.poll()
        with self.assertRaisesRegex(ValueError, "in progress"):
            self.poll(list(reversed(self.views)))
        self.assertTrue(self.finish()["passed"])

    def test_failed_second_view_names_it_and_retains_first_output(self):
        self.scope["prepare_capture"].side_effect = [None, RuntimeError("missing section")]
        result = self.finish()
        self.assertFalse(result["passed"])
        self.assertEqual(result["failed_view"], "section")
        self.assertEqual(result["views"][0]["files"][0]["output"], self.views[0][1])
        self.assertIn("missing section", result["views"][1]["error"])
        self.viewport.SetViewProjection.assert_called_once_with("original camera", True)

    def test_capture_failure_restores_camera_and_stops(self):
        self.scope["collect_capture"] = Mock(side_effect=RuntimeError("GPU failed"))
        result = self.finish()
        self.assertEqual(result["failed_view"], "axon")
        self.assertEqual(result["views"][1]["state"], "pending")
        self.viewport.SetViewProjection.assert_called_once()
        self.assertEqual(self.poll(), result)

    def test_restore_failure_is_reported(self):
        self.viewport.SetViewProjection.return_value = False
        result = self.finish()
        self.assertFalse(result["passed"])
        self.assertIn("camera", result["restore_error"])

    def test_pending_capture_yields_without_advancing_to_next_view(self):
        self.scope["collect_capture"] = Mock(return_value={"capture_task": "WaitingForActivation"})
        self.poll()
        self.poll()
        result = self.poll()
        self.assertFalse(result["complete"])
        self.assertEqual([item["state"] for item in result["views"]], ["capturing", "pending"])
        self.viewport.SetViewProjection.assert_not_called()

    def test_document_switch_stops_and_restores_original_view(self):
        self.poll()
        self.doc.RuntimeSerialNumber = 43
        result = self.poll()
        self.assertTrue(result["complete"])
        self.assertEqual(result["failed_view"], "axon")
        self.assertIn("document changed", result["views"][0]["error"])
        self.viewport.SetViewProjection.assert_called_once()

    def test_existing_or_duplicate_output_is_rejected_before_start(self):
        with open(self.views[0][1], "wb") as stream:
            stream.write(b"keep")
        with self.assertRaisesRegex(ValueError, "existing"):
            self.poll()
        self.scope["prepare_capture"].assert_not_called()
        with self.assertRaisesRegex(ValueError, "uplicate"):
            self.poll([self.views[1], self.views[1]])


class SectionDiscovery(unittest.TestCase):
    def setUp(self):
        self.scope = functions("preparation_status")
        self.sections = NS(SectionPlanes=["native-plane", "native-depth-plane"])
        self.task = NS(IsCompleted=False, IsFaulted=False, IsCanceled=False,
                       Status="WaitingForActivation", Result=self.sections)
        self.initialize = Mock(return_value=NS(IsFaulted=False, IsCanceled=False, Status="RanToCompletion"))
        self.content = NS()
        self.state = dict(section_task=self.task, capture_content=self.content, capture_context="named context",
                          capture_viewport=NS(InitializeContextAsync=self.initialize))
        self.scope.update(sc=NS(sticky={"key": self.state}), KEY="key", find_type=lambda name: name,
                          System=NS(Activator=NS(CreateInstance=lambda t: NS())))

    def test_pending_native_discovery_does_not_wait_or_initialize(self):
        self.assertEqual(self.scope["preparation_status"]()["preparation"], "WaitingForActivation")
        self.initialize.assert_not_called()

    def test_native_named_view_planes_and_depth_reach_render_content(self):
        self.task.IsCompleted = True
        result = self.scope["preparation_status"]()
        self.assertEqual(result["preparation"], "RanToCompletion")
        self.assertEqual(self.content.GlobalSectionPlanes.SectionPlanes, self.sections.SectionPlanes)
        self.initialize.assert_called_once_with("named context", self.content, True)
        self.scope["preparation_status"]()
        self.initialize.assert_called_once()

    def test_failed_discovery_never_initializes_unclipped_render(self):
        self.task.IsFaulted = True
        self.task.Exception = "native discovery failed"
        with self.assertRaisesRegex(RuntimeError, "native discovery failed"):
            self.scope["preparation_status"]()
        self.initialize.assert_not_called()

    def test_empty_native_planes_clear_previous_section(self):
        self.task.IsCompleted = True
        self.sections.SectionPlanes = []
        self.scope["preparation_status"]()
        self.assertEqual(self.content.GlobalSectionPlanes.SectionPlanes, [])


if __name__ == "__main__":
    unittest.main()
