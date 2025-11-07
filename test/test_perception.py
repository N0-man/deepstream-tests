import pytest
from src.simple_ds_pipeline import Perception


@pytest.fixture
def fake_gst(mocker):
    """Mocked Gst module with element factory, pads, and state."""
    Gst = mocker.MagicMock(name="Gst")

    def create_mock_element(name, _):
        element = mocker.MagicMock(name=f"{name}-element")

        sink_pad = mocker.MagicMock(name=f"{name}-sink-pad")
        sink_pad.link.return_value = True

        src_pad = mocker.MagicMock(name=f"{name}-src-pad")
        src_pad.link.return_value = True

        def get_pad(pad_name):
            if "src" in pad_name:
                return src_pad
            return sink_pad

        element.get_static_pad.side_effect = get_pad
        element.request_pad_simple.side_effect = get_pad

        return element

    Gst.ElementFactory.make.side_effect = create_mock_element
    Gst.Pipeline.return_value = mocker.MagicMock(name="pipeline")

    Gst.State = mocker.MagicMock(PLAYING="PLAYING", NULL="NULL")
    Gst.PadProbeType = mocker.MagicMock(BUFFER=1)
    Gst.PadProbeReturn = mocker.MagicMock(OK="OK")
    Gst.MessageType = mocker.MagicMock(ERROR=1, WARNING=2, EOS=3)
    Gst.DebugGraphDetails = mocker.MagicMock(ALL=1)

    return Gst


@pytest.fixture
def fake_glib(mocker):
    """Mocked GLib module."""
    GLib = mocker.MagicMock(name="GLib")
    GLib.MainLoop.return_value = mocker.MagicMock(name="MainLoop")
    return GLib


@pytest.fixture
def fake_pyds(mocker):
    """Mocked pyds module (used in probes)."""
    pyds = mocker.MagicMock(name="pyds")

    pyds.NvDsMetaType.NVDS_USER_META = "USER_META"
    pyds.get_string.side_effect = lambda x: x

    # need to mock all the functions used by pipeline from pyds
    pyds.nvds_acquire_user_meta_from_pool.return_value = mocker.MagicMock(
        name="user_meta_from_pool"
    )
    pyds.NvDsFrameMeta.cast.return_value = mocker.MagicMock(name="frame_meta_cast")

    # Frame meta iteration
    fake_frame_meta = mocker.MagicMock(frame_num=1)
    fake_frame_list = mocker.MagicMock(data=fake_frame_meta, next=None)
    fake_batch_meta = mocker.MagicMock(frame_meta_list=fake_frame_list)
    pyds.gst_buffer_get_nvds_batch_meta.return_value = fake_batch_meta

    return pyds


@pytest.fixture
def runner(fake_gst, fake_glib, fake_pyds):
    return Perception(gst=fake_gst, glib=fake_glib, pyds_module=fake_pyds)


@pytest.mark.only
def test_preception_pipeline_is_created(runner):
    """Test that the perception pipeline is created successfully."""
    pipeline = runner.build_pipeline("input.mp4")
    assert pipeline is not None


def test_pipeline_accepts_video_file_input(runner):
    runner.build_pipeline("input.mp4")
    assert "filesrc" in runner.elements
    filesrc = runner.elements["filesrc"]
    filesrc.set_property.assert_any_call("location", "input.mp4")


def test_preception_pipeline_have_required_element(runner, fake_gst):
    runner.build_pipeline("input.mp4")
    assert "filesrc" in runner.elements
    assert "h264parse" in runner.elements
    assert "nvv4l2decoder" in runner.elements
    assert "nvstreammux" in runner.elements
    assert "queue" in runner.elements
    assert "queue1" in runner.elements
    assert "fakesink" in runner.elements

    assert fake_gst.Pipeline.return_value.add.call_count == len(runner.elements)


def test_required_batching_and_frame_resolution_is_set(runner):
    runner.build_pipeline("input.mp4")
    mux = runner.elements["nvstreammux"]
    mux.set_property.assert_any_call("width", 1280)
    mux.set_property.assert_any_call("height", 720)
    mux.set_property.assert_any_call("batch-size", 1)


def test_video_frames_to_be_parsed_and_decoded(runner):
    runner.build_pipeline("input.mp4")
    filesrc = runner.elements["filesrc"]
    parser = runner.elements["h264parse"]
    decoder = runner.elements["nvv4l2decoder"]

    filesrc.link.assert_called_once_with(parser)
    parser.link.assert_called_once_with(decoder)


def test_frames_should_be_batched_after_decoding(runner):
    runner.build_pipeline("input.mp4")

    decoder = runner.elements["nvv4l2decoder"]
    streammux = runner.elements["nvstreammux"]

    srcpad = decoder.get_static_pad("src")
    sinkpad = streammux.request_pad_simple("sink_0")

    srcpad.link.assert_called_once_with(sinkpad)


def test_single_pipeline_run(runner, fake_gst, mocker):
    runner.build_pipeline("input.mp4")
    result = runner.run(run_loop=False)

    pipeline = runner.pipeline
    state_calls = [
        mocker.call(fake_gst.State.PLAYING),
        mocker.call(fake_gst.State.NULL),
    ]
    pipeline.set_state.assert_has_calls(state_calls, any_order=False)

    assert "pipeline" in result
    assert "elements" in result


def test_fakesink_probe_registration_on_sink_pad(runner, fake_gst):
    runner.build_pipeline("input.mp4")

    fakesink = runner.elements["fakesink"]
    sink_pad = fakesink.get_static_pad("sink")

    sink_pad.add_probe.assert_called_with(
        fake_gst.PadProbeType.BUFFER, runner.fakesink_sink_pad_buffer_probe, None
    )


def test_streammux_probe_registration_on_src_pad(runner, fake_gst):
    runner.build_pipeline("input.mp4")

    streammux = runner.elements["nvstreammux"]
    src_pad = streammux.get_static_pad("src")

    src_pad.add_probe.assert_called_with(
        fake_gst.PadProbeType.BUFFER, runner.streammux_src_pad_buffer_probe, None
    )


def test_build_pipeline_raises_if_element_factory_fails(runner, fake_gst):
    fake_gst.ElementFactory.make.side_effect = [None]
    with pytest.raises(RuntimeError, match="Unable to create element"):
        runner.build_pipeline("input.mp4")


def test_link_failure_with_decoder_pad(runner, fake_gst, mocker):
    def custom_make(name, _):
        element_mock = mocker.MagicMock(name=f"{name}-element")
        if name == "nvv4l2decoder":
            # element to fail...
            element_mock.get_static_pad.return_value = None
        else:
            # others should behave normally
            pad_mock = mocker.MagicMock(name="Pad")
            pad_mock.link.return_value = True
            element_mock.get_static_pad.return_value = pad_mock
        return element_mock

    fake_gst.ElementFactory.make.side_effect = custom_make

    with pytest.raises(RuntimeError, match="nvv4l2decoder src pad linking failed"):
        runner.build_pipeline("input.mp4")


def test_pipeline_run_without_build_fails(runner):
    with pytest.raises(RuntimeError, match="Pipeline not built"):
        runner.run()


def test_streammux_probe_adds_user_custom_meta_data(
    runner, fake_pyds, fake_gst, mocker
):
    pad = mocker.MagicMock()
    info = mocker.MagicMock()
    buffer_obj = mocker.MagicMock(name="buffer")
    info.get_buffer.return_value = buffer_obj

    FRAME_NUMBER = 42

    # Setup frame and batch metadata
    fake_frame_meta = mocker.MagicMock(frame_num=FRAME_NUMBER)
    fake_frame_list = mocker.MagicMock(data=fake_frame_meta, next=None)
    fake_batch_meta = mocker.MagicMock(frame_meta_list=fake_frame_list)

    fake_pyds.gst_buffer_get_nvds_batch_meta.return_value = fake_batch_meta
    fake_pyds.NvDsFrameMeta.cast.return_value = fake_frame_meta

    # Setup data and user_meta
    fake_user_meta = mocker.MagicMock()
    fake_data = mocker.MagicMock()
    fake_pyds.nvds_acquire_user_meta_from_pool.return_value = fake_user_meta
    fake_pyds.alloc_custom_struct.return_value = fake_data

    # WHEN
    ret = runner.streammux_src_pad_buffer_probe(pad, info, None)

    # THEN
    assert ret == fake_gst.PadProbeReturn.OK

    # Verify meta retrieval from buffer
    fake_pyds.gst_buffer_get_nvds_batch_meta.assert_called_once_with(hash(buffer_obj))

    # Verify metadata locking
    fake_pyds.nvds_acquire_meta_lock.assert_called_once_with(fake_batch_meta)
    fake_pyds.nvds_release_meta_lock.assert_called_once_with(fake_batch_meta)

    # Verify frame metadata processing
    fake_pyds.NvDsFrameMeta.cast.assert_called_once_with(fake_frame_meta)
    fake_pyds.nvds_acquire_user_meta_from_pool.assert_called_once_with(fake_batch_meta)

    # Verify custom data properties were set correctly
    assert fake_data.message == f"test message {FRAME_NUMBER}"
    assert fake_data.structId == FRAME_NUMBER
    assert fake_data.sampleInt == FRAME_NUMBER + 1

    # Verify custom data structure creation and population
    fake_pyds.alloc_custom_struct.assert_called_once_with(fake_user_meta)
    fake_pyds.get_string.assert_called_once_with(f"test message {FRAME_NUMBER}")
    # Verify user_meta properties were set
    assert fake_user_meta.user_meta_data == fake_data
    assert fake_user_meta.base_meta.meta_type == fake_pyds.NvDsMetaType.NVDS_USER_META

    # Verify metadata was added to frame
    fake_pyds.nvds_add_user_meta_to_frame.assert_called_once_with(
        fake_frame_meta, fake_user_meta
    )


def test_streammux_probe_skips_when_no_buffer(runner, fake_pyds, fake_gst, mocker):
    pad = mocker.MagicMock()
    info = mocker.MagicMock()
    info.get_buffer.return_value = None

    ret = runner.streammux_src_pad_buffer_probe(pad, info, None)

    assert ret == fake_gst.PadProbeReturn.OK
    fake_pyds.gst_buffer_get_nvds_batch_meta.assert_not_called()
    fake_pyds.nvds_add_user_meta_to_frame.assert_not_called()
