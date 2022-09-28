#!/usr/bin/env python3

import sys
sys.path.append('../')
import gi
import configparser
gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst
from gi.repository import GLib
from ctypes import *
import time
import sys
import numpy as np
import cv2
import math
import platform
import re
from common.is_aarch_64 import is_aarch64
from common.bus_call import bus_call
from common.FPS import PERF_DATA

import pyds
from threading import Thread
from queue import Queue
import os
from datetime import datetime
from statistics import mode

MUXER_OUTPUT_WIDTH=1920
MUXER_OUTPUT_HEIGHT=1080
MUXER_BATCH_TIMEOUT_USEC=4000000
TILED_OUTPUT_WIDTH=1920
TILED_OUTPUT_HEIGHT=1080
GST_CAPS_FEATURES_NVMM="memory:NVMM"
OSD_PROCESS_MODE= 0
OSD_DISPLAY_TEXT= 1
DISPLAY_VIDEO=True

perf_data = None

PGIE_CLASS_ID_CAR = 0

def osd_sink_pad_buffer_probe(pad, info,u_data):
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

def get_frame(gst_buffer, batch_id):
    n_frame=pyds.get_nvds_buf_surface(hash(gst_buffer),batch_id)
    #convert python array into numy array format.
    frame_image=np.array(n_frame,copy=True,order='C')
    #covert the array into cv2 default color format
    frame_image=cv2.cvtColor(frame_image,cv2.COLOR_RGBA2BGRA)
    return frame_image

def cb_newpad(decodebin, decoder_src_pad,data):
    print("In cb_newpad\n")
    caps=decoder_src_pad.get_current_caps()
    gststruct=caps.get_structure(0)
    gstname=gststruct.get_name()
    source_bin=data
    features=caps.get_features(0)

    # Need to check if the pad created by the decodebin is for video and not
    # audio.
    print("gstname=",gstname)
    if(gstname.find("video")!=-1):
        # Link the decodebin pad only if decodebin has picked nvidia
        # decoder plugin nvdec_*. We do this by checking if the pad caps contain
        # NVMM memory features.
        print("features=",features)
        if features.contains("memory:NVMM"):
            # Get the source bin ghost pad
            bin_ghost_pad=source_bin.get_static_pad("src")
            if not bin_ghost_pad.set_target(decoder_src_pad):
                sys.stderr.write("Failed to link decoder src pad to source bin ghost pad\n")
        else:
            sys.stderr.write(" Error: Decodebin did not pick nvidia decoder plugin.\n")

def decodebin_child_added(child_proxy,Object,name,user_data):
    print("Decodebin child added:", name, "\n")
    if(name.find("decodebin") != -1):
        Object.connect("child-added",decodebin_child_added,user_data)   
    if name.find("nvv4l2decoder") != -1:
        if is_aarch64():
            print("Seting bufapi_version\n")
            Object.set_property("bufapi-version",True)

def create_source_bin(index,uri):
    print("Creating source bin")

    # Create a source GstBin to abstract this bin's content from the rest of the
    # pipeline
    bin_name="source-bin-%02d" %index
    print(bin_name)
    nbin=Gst.Bin.new(bin_name)
    if not nbin:
        sys.stderr.write(" Unable to create source bin \n")

    # Source element for reading from the uri.
    # We will use decodebin and let it figure out the container format of the
    # stream and the codec and plug the appropriate demux and decode plugins.
    uri_decode_bin=Gst.ElementFactory.make("uridecodebin", "uri-decode-bin")
    if not uri_decode_bin:
        sys.stderr.write(" Unable to create uri decode bin \n")
    # We set the input uri to the source element
    uri_decode_bin.set_property("uri",uri)
    # Connect to the "pad-added" signal of the decodebin which generates a
    # callback once a new pad for raw data has beed created by the decodebin
    uri_decode_bin.connect("pad-added",cb_newpad,nbin)
    uri_decode_bin.connect("child-added",decodebin_child_added,nbin)

    # We need to create a ghost pad for the source bin which will act as a proxy
    # for the video decoder src pad. The ghost pad will not have a target right
    # now. Once the decode bin creates the video decoder and generates the
    # cb_newpad callback, we will set the ghost pad target to the video decoder
    # src pad.
    Gst.Bin.add(nbin,uri_decode_bin)
    bin_pad=nbin.add_pad(Gst.GhostPad.new_no_target("src",Gst.PadDirection.SRC))
    if not bin_pad:
        sys.stderr.write(" Failed to add ghost pad in source bin \n")
        return None
    return nbin

def main(args,disable_probe=False):
    global DISPLAY_VIDEO

    SOURCES = {
            0:["file:///opt/nvidia/deepstream/deepstream-6.1/sources/deepstream_python_apps/apps/building_multi_dnn_ds/sample_30.mp4","1_1"],
            
        }
    # Check input arguments
    number_sources = len(SOURCES.items())
    
    global perf_data
    perf_data = PERF_DATA(number_sources)
    
    GObject.threads_init()
    Gst.init(None)

    # Create gstreamer elements */
    # Create Pipeline element that will form a connection of other elements
    print("Creating Pipeline \n ")
    pipeline = Gst.Pipeline()
    is_live = False

    if not pipeline:
        sys.stderr.write(" Unable to create Pipeline \n")
    print("Creating streamux \n ")

    # Create nvstreammux instance to form batches from one or more sources.
    streammux = Gst.ElementFactory.make("nvstreammux", "Stream-muxer")
    if not streammux:
        sys.stderr.write(" Unable to create NvStreamMux \n")

    pipeline.add(streammux)
    for i in range(number_sources):
        print("Creating source_bin ",i," \n ")
        uri_name=SOURCES[i][0]
        if uri_name.find("rtsp://") == 0 :
            is_live = True
        source_bin = create_source_bin(i, uri_name)
        if not source_bin:
            sys.stderr.write("Unable to create source bin \n")
        pipeline.add(source_bin)
        padname="sink_%u" %i
        sinkpad= streammux.get_request_pad(padname) 
        if not sinkpad:
            sys.stderr.write("Unable to create sink pad bin \n")
        srcpad=source_bin.get_static_pad("src")
        if not srcpad:
            sys.stderr.write("Unable to create src pad bin \n")
        srcpad.link(sinkpad)

    
    print("Creating Pgie \n ")
    pgie = Gst.ElementFactory.make("nvinfer", "primary-inference")
    if not pgie:
        sys.stderr.write(" Unable to create pgie \n")

    print("Creating tiler \n ")
    tiler=Gst.ElementFactory.make("nvmultistreamtiler", "nvtiler")
    if not tiler:
        sys.stderr.write(" Unable to create tiler \n")

    print("Creating nvvidconv \n ")
    nvvidconv = Gst.ElementFactory.make("nvvideoconvert", "convertor")
    print("Creating filter1 \n ")
    caps1 = Gst.Caps.from_string("video/x-raw(memory:NVMM), format=RGBA")
    filter1 = Gst.ElementFactory.make("capsfilter", "filter1")
    if not filter1:
        sys.stderr.write(" Unable to get the caps filter1 \n")
    filter1.set_property("caps", caps1)
    
    sgie1 = Gst.ElementFactory.make("nvinfer", "secondary1-nvinference-engine")
    if not sgie1:
        sys.stderr.write(" Unable to make sgie1 \n")

    if DISPLAY_VIDEO:
        print("Creating nvosd \n ")
        nvosd = Gst.ElementFactory.make("nvdsosd", "onscreendisplay")
        if not nvosd:
            sys.stderr.write(" Unable to create nvosd \n")
        nvosd.set_property('process-mode',OSD_PROCESS_MODE)
        nvosd.set_property('display-text',OSD_DISPLAY_TEXT)

    # print("Creating EGLSink \n")    
    # sink = Gst.ElementFactory.make("nveglglessink", "nvvideo-renderer")
    print("Creating EGLSink \n")
    if DISPLAY_VIDEO:
        sink = Gst.ElementFactory.make("nveglglessink", "nvvideo-renderer")
    else:
        sink = Gst.ElementFactory.make("fakesink", "fakesink")
    if not sink:
        sys.stderr.write(" Unable to create egl sink \n")
    

    if is_live:
        print("Atleast one of the sources is live")
        streammux.set_property('live-source', 1)

    streammux.set_property('width', 1920)
    streammux.set_property('height', 1080)
    streammux.set_property('batch-size', number_sources)
    streammux.set_property('batched-push-timeout', (1/25)*1000*1000)

    pgie.set_property('config-file-path', 'configs/pgie_config_trafficcamnet.txt')
    pgie_batch_size=pgie.get_property("batch-size")
    if(pgie_batch_size != number_sources):
        print("WARNING: Overriding infer-config batch-size",pgie_batch_size," with number of sources ", number_sources," \n")
        pgie.set_property("batch-size",number_sources)
    sgie1.set_property('config-file-path', 'configs/sgie_config_vehicletypenet.txt')
    sgie1_batch_size=sgie1.get_property("batch-size")
    sgie1.set_property('process-mode', 2)
    if(sgie1_batch_size != number_sources):
        print("WARNING: Overriding infer-config batch-size",sgie1_batch_size," with number of sources ", number_sources," \n")
        sgie1.set_property("batch-size",number_sources)


    tiler_rows=int(math.sqrt(number_sources))
    tiler_columns=int(math.ceil((1.0*number_sources)/tiler_rows))
    tiler.set_property("rows",tiler_rows)
    tiler.set_property("columns",tiler_columns)
    tiler.set_property("width", TILED_OUTPUT_WIDTH)
    tiler.set_property("height", TILED_OUTPUT_HEIGHT)
    sink.set_property("qos",1)
    sink.set_property('sync', 1)

  
    if not is_aarch64():
        # Use CUDA unified memory in the pipeline so frames
        # can be easily accessed on CPU in Python.
        mem_type = int(pyds.NVBUF_MEM_CUDA_UNIFIED)
        streammux.set_property("nvbuf-memory-type", mem_type)
        tiler.set_property("nvbuf-memory-type", mem_type)
        nvvidconv.set_property("nvbuf-memory-type", mem_type)

    print("Adding elements to Pipeline \n")
    pipeline.add(pgie)
    pipeline.add(sgie1)
    pipeline.add(filter1)
    pipeline.add(tiler)
    pipeline.add(nvvidconv)
    if DISPLAY_VIDEO:
        pipeline.add(nvosd)

    #if is_aarch64():
    #    pipeline.add(transform)
    pipeline.add(sink)

    # We link elements in the following order:
    # sourcebin -> streammux -> nvinfer -> nvtracker -> nvdsanalytics ->
    # nvtiler -> nvvideoconvert -> nvdsosd -> sink
    print("Linking elements in the Pipeline \n")
    streammux.link(pgie)
    pgie.link(sgie1)
    #tracker.link(nvanalytics)
    #nvanalytics.link(sgie1)
    sgie1.link(nvvidconv)
    #sgie2.link(nvvidconv)
    nvvidconv.link(filter1)
    #filter1.link(sink)
    filter1.link(tiler)
    
    if DISPLAY_VIDEO:
        tiler.link(nvosd)
        nvosd.link(sink)
           
    else:
        tiler.link(sink)
    # create an event loop and feed gstreamer bus mesages to it
    loop = GObject.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect ("message", bus_call, loop)
    
    
            

    tiler_src_pad = tiler.get_static_pad("sink")
    if not tiler_src_pad:
        sys.stderr.write(" Unable to get src pad \n")
    else:
        tiler_src_pad.add_probe(Gst.PadProbeType.BUFFER, osd_sink_pad_buffer_probe, 0)
        GLib.timeout_add(5000, perf_data.perf_print_callback)

    # List the sources
    print("Now playing...")
    for source in SOURCES.items():
        print("Source: ", source[0], "\ncamera: ", source[1])

    # # print(f"dir(pipeline) {dir(pipeline)}")
    print("Starting pipeline \n")
    # start play back and listed to events      
    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    except:
        pass
    # cleanup
    print("Exiting app\n")
    pipeline.set_state(Gst.State.NULL)



if __name__ == '__main__':

    sys.exit(main(sys.argv))
