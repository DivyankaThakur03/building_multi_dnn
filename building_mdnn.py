
import sys
sys.path.append('../')
import gi
gi.require_version('Gst', '1.0')
from gi.repository import GLib, Gst
from common.is_aarch_64 import is_aarch64
from common.bus_call import bus_call

import pyds

PGIE_CLASS_ID_CAR = 0
PGIE_CLASS_ID_BICYCLE = 1
PGIE_CLASS_ID_PERSON = 2
PGIE_CLASS_ID_ROADSIGN = 3


def osd_sink_pad_buffer_probe(pad, info):
    gst_buffer = info.get_buffer()

    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    l_frame = batch_meta.frame_meta_list

    while l_frame is not None:
        try:
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
        except StopIteration:
            break

        frame_num=frame_meta.frame_num
        num_obj = frame_meta.num_obj_meta
        l_obj=frame_meta.obj_meta_list
        
        print("Frame Number={} Number of Objects={}".format(frame_num, num_obj))
        
        while l_obj is not None:
            try:
                obj_meta=pyds.NvDsObjectMeta.cast(l_obj.data)
                
                # Define an analyze_meta function to manipulate metadata
                analyze_meta(obj_meta)
            except StopIteration:
                break
                
            try: 
                l_obj=l_obj.next
            except StopIteration:
                break
        
        try:
            l_frame=l_frame.next
        except StopIteration:
            break
    return Gst.PadProbeReturn.OK

def analyze_meta(obj_meta): 
    # Only car supports secondary inference
    if obj_meta.class_id == PGIE_CLASS_ID_CAR:     
        cls_meta=obj_meta.classifier_meta_list
        
        # Iterate through each class meta until the end
        while cls_meta is not None:
            cls=pyds.NvDsClassifierMeta.cast(cls_meta.data)
            # Get label info
            label_info=cls.label_info_list  
            
            # Iterate through each label info meta until the end
            while label_info is not None:
                # Cast data type of label from pyds.GList
                label_meta=pyds.glist_get_nvds_label_info(label_info.data)
                if cls.unique_component_id==2:
                    print('\t Type & Probability = {}% {}'.format(round(label_meta.result_prob*100), label_meta.result_label))
                try:
                    label_info=label_info.next
                except StopIteration:
                    break
            
            try:
                cls_meta=cls_meta.next
            except StopIteration:
                break
    return None


def main(args):
    # Check input arguments
    if len(args) != 2:
        sys.stderr.write("usage: %s <media file or uri>\n" % args[0])
        sys.exit(1)

    # Standard GStreamer initialization
    Gst.init(None)

    print("Creating Pipeline \n ")
    pipeline = Gst.Pipeline()

    if not pipeline:
        sys.stderr.write(" Unable to create Pipeline \n")

    source = Gst.ElementFactory.make("filesrc", "file-source")
    if not source:
        sys.stderr.write(" Unable to create Source \n")

    print("Creating H264Parser \n")
    h264parser = Gst.ElementFactory.make("h264parse", "h264-parser")
    if not h264parser:
        sys.stderr.write(" Unable to create h264 parser \n")

    print("Creating Decoder \n")
    decoder = Gst.ElementFactory.make("nvv4l2decoder", "nvv4l2-decoder")
    if not decoder:
        sys.stderr.write(" Unable to create Nvv4l2 Decoder \n")

    streammux = Gst.ElementFactory.make("nvstreammux", "Stream-muxer")
    if not streammux:
        sys.stderr.write(" Unable to create NvStreamMux \n")
    streammux.set_property('width', 888) 
    streammux.set_property('height', 696) 
    streammux.set_property('batch-size', 1)

    pgie=Gst.ElementFactory.make("nvinfer", "primary-inference")
    pgie.set_property('config-file-path','configs/pgie_config_trafficcamnet.txt')

    sgie=Gst.ElementFactory.make("nvinfer","secondary-inference")
    sgie.set_property('config-file-path','configs/sgie_config_vehicletypenet.txt')

    nvvidconv1 = Gst.ElementFactory.make("nvvideoconvert", "convertor1")
    if not nvvidconv1:
        sys.stderr.write(" Unable to create nvvidconv1 \n")

    nvosd = Gst.ElementFactory.make("nvdsosd", "onscreendisplay")

    if not nvosd:
        sys.stderr.write(" Unable to create nvosd \n")

    nvvidconv2 = Gst.ElementFactory.make("nvvideoconvert", "convertor2")
    if not nvvidconv2:
        sys.stderr.write(" Unable to create nvvidconv2 \n")

    print("Creating EGLSink \n")
    sink = Gst.ElementFactory.make("nveglglessink", "nvvideo-renderer")
    if not sink:
        sys.stderr.write(" Unable to create egl sink \n")

    print("Playing file %s " %args[1])


    pipeline.add(source)
    pipeline.add(h264parser)
    pipeline.add(decoder)
    pipeline.add(streammux)
    pipeline.add(pgie)
    pipeline.add(sgie)
    pipeline.add(nvvidconv1)
    pipeline.add(nvosd)
    pipeline.add(nvvidconv2)
    pipeline.add(sink)    

    print("Linking elements in the Pipeline \n")
    
    source.link(h264parser)
    h264parser.link(decoder)
    decoder.link(streammux)
    streammux.link(pgie)
    pgie.link(sgie)
    sgie.link(nvvidconv1)
    nvvidconv1.link(nvosd)
    nvosd.link(nvvidconv2)
    nvvidconv2.link(sink)
    
    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect ("message", bus_call, loop)

    # Lets add probe to get informed of the meta data generated, we add probe to
    # the sink pad of the osd element, since by that time, the buffer would have
    # had got all the metadata.
    osdsinkpad = nvosd.get_static_pad("sink")
    if not osdsinkpad:
        sys.stderr.write(" Unable to get sink pad of nvosd \n")

    osdsinkpad.add_probe(Gst.PadProbeType.BUFFER, osd_sink_pad_buffer_probe, 0)

    print("Starting pipeline \n")
    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    except:
        pass
    # cleanup
    pipeline.set_state(Gst.State.NULL)

if __name__ == '__main__':
    sys.exit(main(sys.argv))