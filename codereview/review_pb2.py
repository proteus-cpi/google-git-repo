#!/usr/bin/python2.4
# Generated by the protocol buffer compiler.  DO NOT EDIT!

from froofle.protobuf import descriptor
from froofle.protobuf import message
from froofle.protobuf import reflection
from froofle.protobuf import service
from froofle.protobuf import service_reflection
from froofle.protobuf import descriptor_pb2


import upload_bundle_pb2



_REVIEWSERVICE = descriptor.ServiceDescriptor(
  name='ReviewService',
  full_name='codereview.ReviewService',
  index=0,
  options=None,
  methods=[
  descriptor.MethodDescriptor(
    name='UploadBundle',
    full_name='codereview.ReviewService.UploadBundle',
    index=0,
    containing_service=None,
    input_type=upload_bundle_pb2._UPLOADBUNDLEREQUEST,
    output_type=upload_bundle_pb2._UPLOADBUNDLERESPONSE,
    options=None,
  ),
  descriptor.MethodDescriptor(
    name='ContinueBundle',
    full_name='codereview.ReviewService.ContinueBundle',
    index=1,
    containing_service=None,
    input_type=upload_bundle_pb2._UPLOADBUNDLECONTINUE,
    output_type=upload_bundle_pb2._UPLOADBUNDLERESPONSE,
    options=None,
  ),
])

class ReviewService(service.Service):
  __metaclass__ = service_reflection.GeneratedServiceType
  DESCRIPTOR = _REVIEWSERVICE
class ReviewService_Stub(ReviewService):
  __metaclass__ = service_reflection.GeneratedServiceStubType
  DESCRIPTOR = _REVIEWSERVICE
