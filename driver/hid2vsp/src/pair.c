/*++

Module Name:

    pair.c

Abstract:

    Helpers backing pair.h. The two halves of a paired port install
    as separate root-enumerated devices (`Root\hid2vsp_pair_a` and
    `_b`); they meet here, in the singleton DRIVER_CONTEXT slots.

    The first design tried to use a UMDF software bus driver with
    WdfFdoInitSetDefaultChildListConfig + WdfPdoInit*. UMDF v2 does
    not export those APIs (they are KMDF-only), so we install both
    halves as Root devices instead and rely on
    `UmdfHostProcessSharing = ProcessSharingEnabled` to keep them in
    a single WUDFHost where these in-process pointers are valid.

--*/

#include "internal.h"


PAIR_SIDE
PairSideFromHardwareId(
    _In_ PCWSTR HardwareId
    )
{
    size_t  len;
    WCHAR   last;

    if (HardwareId == NULL) {
        return PAIR_SIDE_NONE;
    }

    //
    // Match the trailing `_a` / `_b` rather than the full Root\... so
    // we are tolerant of capitalization variations PnP may produce.
    //
    len = wcslen(HardwareId);
    if (len < 2) {
        return PAIR_SIDE_NONE;
    }
    last = HardwareId[len - 1];
    if (last == L'a' || last == L'A') {
        return PAIR_SIDE_A;
    }
    if (last == L'b' || last == L'B') {
        return PAIR_SIDE_B;
    }
    return PAIR_SIDE_NONE;
}


NTSTATUS
PairAttachChild(
    _In_  struct _DEVICE_CONTEXT *ChildContext,
    _In_  PCWSTR                  ChildHardwareId,
    _Out_ PAIR_SIDE              *Side
    )
{
    PDRIVER_CONTEXT drvCtx;
    PAIR_SIDE       side;
    NTSTATUS        status = STATUS_SUCCESS;

    *Side = PAIR_SIDE_NONE;

    side = PairSideFromHardwareId(ChildHardwareId);
    if (side == PAIR_SIDE_NONE) {
        Trace(TRACE_LEVEL_ERROR,
            "PairAttachChild: cannot derive side from '%ws'",
            ChildHardwareId ? ChildHardwareId : L"(null)");
        return STATUS_INVALID_PARAMETER;
    }

    drvCtx = GetDriverContextHandle(WdfGetDriver());

    WdfWaitLockAcquire(drvCtx->PairLock, NULL);

    if (side == PAIR_SIDE_A) {
        if (drvCtx->ChildA != NULL) {
            Trace(TRACE_LEVEL_ERROR,
                "PairAttachChild: side A slot already taken");
            status = STATUS_DEVICE_ALREADY_ATTACHED;
        } else {
            drvCtx->ChildA = ChildContext;
        }
    } else {
        if (drvCtx->ChildB != NULL) {
            Trace(TRACE_LEVEL_ERROR,
                "PairAttachChild: side B slot already taken");
            status = STATUS_DEVICE_ALREADY_ATTACHED;
        } else {
            drvCtx->ChildB = ChildContext;
        }
    }

    if (NT_SUCCESS(status)) {
        ChildContext->Side = side;
    }

    WdfWaitLockRelease(drvCtx->PairLock);

    if (NT_SUCCESS(status)) {
        *Side = side;
        Trace(TRACE_LEVEL_INFO,
            "PairAttachChild: side %s attached",
            side == PAIR_SIDE_A ? "A" : "B");
    }
    return status;
}


VOID
PairDetachChild(
    _In_ struct _DEVICE_CONTEXT *ChildContext
    )
{
    PDRIVER_CONTEXT drvCtx = GetDriverContextHandle(WdfGetDriver());

    WdfWaitLockAcquire(drvCtx->PairLock, NULL);

    if (drvCtx->ChildA == ChildContext) {
        drvCtx->ChildA = NULL;
    } else if (drvCtx->ChildB == ChildContext) {
        drvCtx->ChildB = NULL;
    }

    WdfWaitLockRelease(drvCtx->PairLock);
}


// Old API kept for source compatibility — DO NOT USE in new code.
// Releases the lock before returning, so the peer pointer is stale
// the moment it's dereferenced. New call sites should use
// PairAcquirePeer + PairReleasePeer instead.
struct _DEVICE_CONTEXT *
PairGetPeer(
    _In_ struct _DEVICE_CONTEXT *ChildContext
    )
{
    struct _DEVICE_CONTEXT *peer = PairAcquirePeer(ChildContext);
    PairReleasePeer();
    return peer;
}


//
// Acquires the driver-wide PairLock and returns the peer pointer
// while still holding the lock. Caller MUST call PairReleasePeer()
// once it is done dereferencing the returned pointer — otherwise the
// peer's WDFDEVICE can be torn down between this call and the access,
// leaving the caller dereferencing freed QueueContext / RingBuffer
// memory.
//
// Returns NULL if no peer is attached. The lock is held in either
// case; the caller still must release it.
//
struct _DEVICE_CONTEXT *
PairAcquirePeer(
    _In_ struct _DEVICE_CONTEXT *ChildContext
    )
{
    PDRIVER_CONTEXT drvCtx = GetDriverContextHandle(WdfGetDriver());

    WdfWaitLockAcquire(drvCtx->PairLock, NULL);

    if (ChildContext->Side == PAIR_SIDE_A) {
        return drvCtx->ChildB;
    }
    if (ChildContext->Side == PAIR_SIDE_B) {
        return drvCtx->ChildA;
    }
    return NULL;
}


VOID
PairReleasePeer(VOID)
{
    PDRIVER_CONTEXT drvCtx = GetDriverContextHandle(WdfGetDriver());
    WdfWaitLockRelease(drvCtx->PairLock);
}
