/*++

Module Name:

    pair.h

Abstract:

    Helpers for pairing two hid2vsp devices (sides A and B) so writes
    on one come out as reads on the other.

    Architecture: each "pair" is two independent root-enumerated UMDF
    devices (`Root\hid2vsp_pair_a` and `Root\hid2vsp_pair_b`), both
    served by the same driver service, co-located in a single
    WUDFHost via `UmdfHostProcessSharing = ProcessSharingEnabled`.
    Rendezvous happens through the singleton DRIVER_CONTEXT (declared
    in driver.h) which holds slots for ChildA and ChildB.

    Phase 1 had a single port with self-loopback; Phase 2 splits that
    into two ports forming a pipe.

    Data flow:
        side-A write -> bytes appended to A's TX ring (queue context
                        on A); we then wake any reads parked on B.
        side-B read  -> pops bytes from A's TX ring (peer side).
        (mirror for the reverse direction.)

--*/

#pragma once

typedef enum _PAIR_SIDE {
    PAIR_SIDE_NONE = 0,
    PAIR_SIDE_A    = 1,
    PAIR_SIDE_B    = 2,
} PAIR_SIDE;

//
// Hardware-ID suffixes for the two halves. The function driver looks
// at its own hw ID to learn which side it is.
//
#define PAIR_HWID_SIDE_A        L"Root\\hid2vsp_pair_a"
#define PAIR_HWID_SIDE_B        L"Root\\hid2vsp_pair_b"

//
// Forward decls. The DEVICE_CONTEXT type is defined in device.h, which
// is included after this header. We use the bare struct pointer in
// these protos so pair.h does not depend on the typedef order.
//
struct _DEVICE_CONTEXT;
struct _QUEUE_CONTEXT;

//
// Determine which side a child belongs to from its hardware ID.
//
PAIR_SIDE
PairSideFromHardwareId(
    _In_ PCWSTR HardwareId
    );

//
// Register a child device in the driver-wide pair table. *Side is set
// to the side parsed from the hardware ID. Fails if both slots for
// that side are already taken.
//
NTSTATUS
PairAttachChild(
    _In_  struct _DEVICE_CONTEXT *ChildContext,
    _In_  PCWSTR                  ChildHardwareId,
    _Out_ PAIR_SIDE              *Side
    );

VOID
PairDetachChild(
    _In_  struct _DEVICE_CONTEXT *ChildContext
    );

//
// Legacy lookup. Acquires + releases the pair lock internally and
// returns the peer pointer. The pointer is stale the instant the
// function returns, so it is unsafe to dereference unless the caller
// has another guarantee that the peer can't go away (none currently
// exists). Kept for source compatibility; new code should use the
// scoped Acquire / Release pair below.
//
struct _DEVICE_CONTEXT *
PairGetPeer(
    _In_  struct _DEVICE_CONTEXT *ChildContext
    );

//
// Scoped peer access. Acquires the driver-wide PairLock and returns
// the peer pointer (or NULL). The caller MUST call PairReleasePeer()
// once it is done dereferencing the pointer; the lock is held until
// then so the peer's WDFDEVICE / QueueContext / RingBuffer cannot be
// torn down mid-access.
//
// Usage:
//     peer = PairAcquirePeer(devCtx);
//     if (peer && peer->QueueContext) {
//         // safe to touch peer->QueueContext->RingBuffer here
//     }
//     PairReleasePeer();
//
struct _DEVICE_CONTEXT *
PairAcquirePeer(
    _In_  struct _DEVICE_CONTEXT *ChildContext
    );

VOID
PairReleasePeer(VOID);
