/*++

Copyright (C) Microsoft Corporation, All Rights Reserved

Module Name:

    Driver.h

Abstract:

    This module contains the type definitions for the VirtualSerial sample's
    driver callback class.

Environment:

    Windows Driver Framework

--*/

#pragma once

//
// This class handles driver events for the VirtualSerial sample.  In particular
// it supports the OnDeviceAdd event, which occurs when the driver is called
// to setup per-device handlers for a new device stack.
//

DRIVER_INITIALIZE           DriverEntry;

EVT_WDF_DRIVER_DEVICE_ADD   EvtDeviceAdd;

//
// Driver-wide context. UMDF v2 does not expose software-bus APIs, so
// the two halves of a pair install as independent root-enumerated
// devices (`Root\hid2vsp_pair_a` and `_b`) and rendezvous via this
// driver-wide singleton. Single-pair is supported today; multi-pair
// would key by an instance ID parsed from the hardware ID.
//
typedef struct _DRIVER_CONTEXT
{
    WDFWAITLOCK     PairLock;       // protects ChildA / ChildB

    struct _DEVICE_CONTEXT *ChildA; // side-A's device context, or NULL
    struct _DEVICE_CONTEXT *ChildB; // side-B's device context, or NULL

} DRIVER_CONTEXT, *PDRIVER_CONTEXT;

WDF_DECLARE_CONTEXT_TYPE_WITH_NAME(DRIVER_CONTEXT, GetDriverContextHandle);

NTSTATUS
ChildEvtDeviceAdd(
    _In_    WDFDRIVER       Driver,
    _Inout_ PWDFDEVICE_INIT DeviceInit
    );
