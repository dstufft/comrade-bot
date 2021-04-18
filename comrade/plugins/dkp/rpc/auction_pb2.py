# -*- coding: utf-8 -*-
# Generated by the protocol buffer compiler.  DO NOT EDIT!
# source: comrade/plugins/dkp/rpc/auction.proto
"""Generated protocol buffer code."""
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from google.protobuf import reflection as _reflection
from google.protobuf import symbol_database as _symbol_database
# @@protoc_insertion_point(imports)

_sym_db = _symbol_database.Default()




DESCRIPTOR = _descriptor.FileDescriptor(
  name='comrade/plugins/dkp/rpc/auction.proto',
  package='auction',
  syntax='proto3',
  serialized_options=None,
  create_key=_descriptor._internal_create_key,
  serialized_pb=b'\n%comrade/plugins/dkp/rpc/auction.proto\x12\x07\x61uction\"B\n\x0e\x41\x64\x64ItemRequest\x12\x10\n\x08\x61\x64\x64\x65\x64_by\x18\x01 \x01(\t\x12\x0c\n\x04item\x18\x02 \x01(\t\x12\x10\n\x08quantity\x18\x03 \x01(\x05\"\x11\n\x0f\x41\x64\x64ItemResponse2I\n\x07\x41uction\x12>\n\x07\x41\x64\x64Item\x12\x17.auction.AddItemRequest\x1a\x18.auction.AddItemResponse\"\x00\x62\x06proto3'
)




_ADDITEMREQUEST = _descriptor.Descriptor(
  name='AddItemRequest',
  full_name='auction.AddItemRequest',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  create_key=_descriptor._internal_create_key,
  fields=[
    _descriptor.FieldDescriptor(
      name='added_by', full_name='auction.AddItemRequest.added_by', index=0,
      number=1, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=b"".decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='item', full_name='auction.AddItemRequest.item', index=1,
      number=2, type=9, cpp_type=9, label=1,
      has_default_value=False, default_value=b"".decode('utf-8'),
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
    _descriptor.FieldDescriptor(
      name='quantity', full_name='auction.AddItemRequest.quantity', index=2,
      number=3, type=5, cpp_type=1, label=1,
      has_default_value=False, default_value=0,
      message_type=None, enum_type=None, containing_type=None,
      is_extension=False, extension_scope=None,
      serialized_options=None, file=DESCRIPTOR,  create_key=_descriptor._internal_create_key),
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  serialized_options=None,
  is_extendable=False,
  syntax='proto3',
  extension_ranges=[],
  oneofs=[
  ],
  serialized_start=50,
  serialized_end=116,
)


_ADDITEMRESPONSE = _descriptor.Descriptor(
  name='AddItemResponse',
  full_name='auction.AddItemResponse',
  filename=None,
  file=DESCRIPTOR,
  containing_type=None,
  create_key=_descriptor._internal_create_key,
  fields=[
  ],
  extensions=[
  ],
  nested_types=[],
  enum_types=[
  ],
  serialized_options=None,
  is_extendable=False,
  syntax='proto3',
  extension_ranges=[],
  oneofs=[
  ],
  serialized_start=118,
  serialized_end=135,
)

DESCRIPTOR.message_types_by_name['AddItemRequest'] = _ADDITEMREQUEST
DESCRIPTOR.message_types_by_name['AddItemResponse'] = _ADDITEMRESPONSE
_sym_db.RegisterFileDescriptor(DESCRIPTOR)

AddItemRequest = _reflection.GeneratedProtocolMessageType('AddItemRequest', (_message.Message,), {
  'DESCRIPTOR' : _ADDITEMREQUEST,
  '__module__' : 'comrade.plugins.dkp.rpc.auction_pb2'
  # @@protoc_insertion_point(class_scope:auction.AddItemRequest)
  })
_sym_db.RegisterMessage(AddItemRequest)

AddItemResponse = _reflection.GeneratedProtocolMessageType('AddItemResponse', (_message.Message,), {
  'DESCRIPTOR' : _ADDITEMRESPONSE,
  '__module__' : 'comrade.plugins.dkp.rpc.auction_pb2'
  # @@protoc_insertion_point(class_scope:auction.AddItemResponse)
  })
_sym_db.RegisterMessage(AddItemResponse)



_AUCTION = _descriptor.ServiceDescriptor(
  name='Auction',
  full_name='auction.Auction',
  file=DESCRIPTOR,
  index=0,
  serialized_options=None,
  create_key=_descriptor._internal_create_key,
  serialized_start=137,
  serialized_end=210,
  methods=[
  _descriptor.MethodDescriptor(
    name='AddItem',
    full_name='auction.Auction.AddItem',
    index=0,
    containing_service=None,
    input_type=_ADDITEMREQUEST,
    output_type=_ADDITEMRESPONSE,
    serialized_options=None,
    create_key=_descriptor._internal_create_key,
  ),
])
_sym_db.RegisterServiceDescriptor(_AUCTION)

DESCRIPTOR.services_by_name['Auction'] = _AUCTION

# @@protoc_insertion_point(module_scope)