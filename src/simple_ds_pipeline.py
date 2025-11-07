import sys
from dependency_injector.wiring import Provide, inject
from .deepstream_container import DeepstreamContainer


class Perception:
    @inject
    def __init__(
        self,
        gst=Provide[DeepstreamContainer.gst],
        glib=Provide[DeepstreamContainer.glib],
        pyds_module=Provide[DeepstreamContainer.pyds],
        callbacks=None,
    ):
        self.Gst = gst
        self.GLib = glib
        self.pyds = pyds_module

        self.callbacks = {
            "bus_call": self.bus_call,
            "mux_probe": self.streammux_src_pad_buffer_probe,
            "sink_probe": self.fakesink_sink_pad_buffer_probe,
        }
        if callbacks:
            self.callbacks.update(callbacks)

        self.pipeline = None
        self.elements = {}

    def build_pipeline(self, video_file_path: str):
        Gst = self.Gst
        Gst.init(None)

        self.pipeline = Gst.Pipeline()
        if not self.pipeline:
            raise RuntimeError("Unable to create Pipeline")

        # Create elements
        names = [
            ("filesrc", "file-source"),
            ("h264parse", "h264-parser"),
            ("nvv4l2decoder", "nvv4l2-decoder"),
            ("nvstreammux", "Stream-muxer"),
            ("queue", "queue"),
            ("queue1", "queue1"),
            ("fakesink", "fakesink"),
        ]
        for factory, name in names:
            el = Gst.ElementFactory.make(factory, name)
            if not el:
                raise RuntimeError(f"Unable to create element: {factory}")
            self.elements[factory] = el
            self.pipeline.add(el)

        # Configure elements
        source = self.elements["filesrc"]
        mux = self.elements["nvstreammux"]
        source.set_property("location", video_file_path)
        mux.set_property("width", 1280)
        mux.set_property("height", 720)
        mux.set_property("batch-size", 1)

        # Link elements
        source.link(self.elements["h264parse"])
        self.elements["h264parse"].link(self.elements["nvv4l2decoder"])

        sinkpad = mux.request_pad_simple("sink_0")
        srcpad = self.elements["nvv4l2decoder"].get_static_pad("src")
        if not srcpad or not sinkpad:
            raise RuntimeError("nvv4l2decoder src pad linking failed")
        srcpad.link(sinkpad)

        mux.link(self.elements["queue"])
        self.elements["queue"].link(self.elements["queue1"])
        self.elements["queue1"].link(self.elements["fakesink"])

        # Attach probes
        streammux_src_pad = mux.get_static_pad("src")
        streammux_src_pad.add_probe(
            Gst.PadProbeType.BUFFER, self.callbacks["mux_probe"], None
        )

        fakesink_sink_pad = self.elements["fakesink"].get_static_pad("sink")
        fakesink_sink_pad.add_probe(
            Gst.PadProbeType.BUFFER, self.callbacks["sink_probe"], None
        )

        return self.pipeline

    def run(self, run_loop=True):
        """Run the pipeline and optionally the main loop."""
        if not self.pipeline:
            raise RuntimeError("Pipeline not built. Call build_pipeline().")

        Gst = self.Gst
        GLib = self.GLib

        loop = GLib.MainLoop()
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.callbacks["bus_call"], loop)

        Gst.debug_bin_to_dot_file(self.pipeline, Gst.DebugGraphDetails.ALL, "graph")
        Gst.info("Starting pipeline")
        self.pipeline.set_state(Gst.State.PLAYING)

        print("Pipeline playing")
        if run_loop:
            try:
                loop.run()
            except Exception:
                pass

        self.pipeline.set_state(Gst.State.NULL)
        return {"pipeline": self.pipeline, "elements": self.elements}

    def bus_call(self, bus, message, loop):
        Gst = self.Gst
        t = message.type
        if t == Gst.MessageType.EOS:
            Gst.info("End-of-stream")
            loop.quit()
        elif t == Gst.MessageType.WARNING:
            err, debug = message.parse_warning()
            Gst.warning(f"Warning: {err}: {debug}")
        elif t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            Gst.error(f"Error: {err}: {debug}")
            loop.quit()
        return True

    def streammux_src_pad_buffer_probe(self, pad, info, u_data):
        Gst = self.Gst
        pyds = self.pyds
        gst_buffer = info.get_buffer()
        if not gst_buffer:
            Gst.warning("Unable to get GstBuffer")
            return Gst.PadProbeReturn.OK

        batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
        if not batch_meta:
            return Gst.PadProbeReturn.OK

        pyds.nvds_acquire_meta_lock(batch_meta)
        l_frame = batch_meta.frame_meta_list

        while l_frame is not None:
            try:
                frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
                frame_number = frame_meta.frame_num
            except StopIteration:
                continue

            user_meta = pyds.nvds_acquire_user_meta_from_pool(batch_meta)
            if user_meta:
                test_string = f"test message {frame_number}"
                data = pyds.alloc_custom_struct(user_meta)
                data.message = pyds.get_string(test_string)
                data.structId = frame_number
                data.sampleInt = frame_number + 1
                user_meta.user_meta_data = data
                user_meta.base_meta.meta_type = pyds.NvDsMetaType.NVDS_USER_META
                pyds.nvds_add_user_meta_to_frame(frame_meta, user_meta)
            try:
                l_frame = l_frame.next
            except StopIteration:
                break

        pyds.nvds_release_meta_lock(batch_meta)
        return Gst.PadProbeReturn.OK

    def fakesink_sink_pad_buffer_probe(self, pad, info, u_data):
        Gst = self.Gst
        pyds = self.pyds
        gst_buffer = info.get_buffer()
        if not gst_buffer:
            print("Unable to get GstBuffer")
            return Gst.PadProbeReturn.OK

        batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
        if not batch_meta:
            return Gst.PadProbeReturn.OK

        pyds.nvds_acquire_meta_lock(batch_meta)
        l_frame = batch_meta.frame_meta_list

        while l_frame is not None:
            try:
                frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
            except StopIteration:
                continue

            l_usr = frame_meta.frame_user_meta_list
            while l_usr is not None:
                try:
                    user_meta = pyds.NvDsUserMeta.cast(l_usr.data)
                except StopIteration:
                    continue

                if user_meta.base_meta.meta_type == pyds.NvDsMetaType.NVDS_USER_META:
                    custom = pyds.CustomDataStruct.cast(user_meta.user_meta_data)
                    Gst.info(
                        f"event msg meta, otherAttrs = {pyds.get_string(custom.message)}"
                    )
                    print("custom meta structId::", custom.structId)
                    print("custom meta msg::", pyds.get_string(custom.message))
                    print("custom meta sampleInt::", custom.sampleInt)

                try:
                    l_usr = l_usr.next
                except StopIteration:
                    break

            try:
                l_frame = l_frame.next
            except StopIteration:
                break

        pyds.nvds_release_meta_lock(batch_meta)
        return Gst.PadProbeReturn.OK


def main(args):
    deepstreamContainer = DeepstreamContainer()
    deepstreamContainer.wire(modules=[__name__])

    perception = Perception()
    perception.build_pipeline("sample_1080p_h264.mp4")
    perception.run()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
