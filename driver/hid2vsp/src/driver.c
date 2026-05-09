/*++

Module Name:

    driver.c

Abstract:

    Driver entry + AddDevice. Phase 2 installs two independent
    root-enumerated devices per pair (sides A and B); both load this
    same DLL. EvtDeviceAdd is the same for both — pair side is
    derived from the hardware ID inside ChildEvtDeviceAdd.

Environment:

    Windows Driver Framework, UMDF v2

--*/

#include <initguid.h>
#include "internal.h"


NTSTATUS
DriverEntry(
    _In_  PDRIVER_OBJECT    DriverObject,
    _In_  PUNICODE_STRING   RegistryPath
    )
{
    NTSTATUS                status;
    WDF_DRIVER_CONFIG       driverConfig;
    WDF_OBJECT_ATTRIBUTES   driverAttrs;
    WDFDRIVER               driver;
    PDRIVER_CONTEXT         drvCtx;
    WDF_OBJECT_ATTRIBUTES   lockAttrs;

    WDF_DRIVER_CONFIG_INIT(&driverConfig, EvtDeviceAdd);

    WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(&driverAttrs, DRIVER_CONTEXT);

    status = WdfDriverCreate(DriverObject,
                            RegistryPath,
                            &driverAttrs,
                            &driverConfig,
                            &driver);
    if (!NT_SUCCESS(status)) {
        Trace(TRACE_LEVEL_ERROR,
            "Error: WdfDriverCreate failed 0x%x", status);
        return status;
    }

    drvCtx = GetDriverContextHandle(driver);

    WDF_OBJECT_ATTRIBUTES_INIT(&lockAttrs);
    lockAttrs.ParentObject = driver;
    status = WdfWaitLockCreate(&lockAttrs, &drvCtx->PairLock);
    if (!NT_SUCCESS(status)) {
        Trace(TRACE_LEVEL_ERROR,
            "Error: WdfWaitLockCreate (driver) failed 0x%x", status);
        return status;
    }

    return STATUS_SUCCESS;
}


NTSTATUS
EvtDeviceAdd(
    _In_    WDFDRIVER         Driver,
    _Inout_ PWDFDEVICE_INIT   DeviceInit
    )
{
    return ChildEvtDeviceAdd(Driver, DeviceInit);
}
