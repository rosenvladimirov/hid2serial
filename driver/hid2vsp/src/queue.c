/*++

Copyright (c) Microsoft Corporation, All Rights Reserved

Module Name:

    Queue.c

Abstract:

    This file implements the I/O queue interface and performs
    the read/write/ioctl operations.

Environment:

    Windows Driver Framework

--*/


#include "internal.h"

NTSTATUS
QueueCreate(
    _In_  PDEVICE_CONTEXT   DeviceContext
    )
{
    NTSTATUS                status;
    WDFDEVICE               device = DeviceContext->Device;
    WDF_IO_QUEUE_CONFIG     queueConfig;
    WDF_OBJECT_ATTRIBUTES   queueAttributes;
    WDFQUEUE                queue;
    PQUEUE_CONTEXT          queueContext;

    //
    // Create the default queue
    //

    // Sequential dispatch — the ring buffer is single-producer /
    // single-consumer per side (see ringbuffer.h header). A parallel
    // queue would race two reads (or two writes) of the same side
    // against each other on the head/tail pointers. Sequential
    // serialises within one side; the OTHER side runs on its own
    // device's queue, so the pair is not bottlenecked.
    WDF_IO_QUEUE_CONFIG_INIT_DEFAULT_QUEUE(
                            &queueConfig,
                            WdfIoQueueDispatchSequential);

    queueConfig.EvtIoRead           = EvtIoRead;
    queueConfig.EvtIoWrite          = EvtIoWrite;
    queueConfig.EvtIoDeviceControl  = EvtIoDeviceControl;

    WDF_OBJECT_ATTRIBUTES_INIT_CONTEXT_TYPE(
                            &queueAttributes,
                            QUEUE_CONTEXT);

    status = WdfIoQueueCreate(
                            device,
                            &queueConfig,
                            &queueAttributes,
                            &queue);

    if( !NT_SUCCESS(status) ) {
        Trace(TRACE_LEVEL_ERROR,
            "Error: WdfIoQueueCreate failed 0x%x", status);
        return status;
    }

    queueContext = GetQueueContext(queue);
    queueContext->Queue = queue;
    queueContext->DeviceContext = DeviceContext;
    DeviceContext->QueueContext = queueContext;

    //
    // Create a manual queue to hold pending read requests. By keeping
    // them in the queue, framework takes care of cancelling them if the app
    // exits
    //

    WDF_IO_QUEUE_CONFIG_INIT(
                            &queueConfig,
                            WdfIoQueueDispatchManual);

    status = WdfIoQueueCreate(
                            device,
                            &queueConfig,
                            WDF_NO_OBJECT_ATTRIBUTES,
                            &queue);

    if( !NT_SUCCESS(status) ) {
        Trace(TRACE_LEVEL_ERROR,
            "Error: WdfIoQueueCreate manual queue failed 0x%x", status);
        return status;
    }

    queueContext->ReadQueue = queue;

    //
    // Create another manual queue to hold pending IOCTL_SERIAL_WAIT_ON_MASK
    //

    WDF_IO_QUEUE_CONFIG_INIT(
                            &queueConfig,
                            WdfIoQueueDispatchManual);

    status = WdfIoQueueCreate(
                            device,
                            &queueConfig,
                            WDF_NO_OBJECT_ATTRIBUTES,
                            &queue);

    if( !NT_SUCCESS(status) ) {
        Trace(TRACE_LEVEL_ERROR,
            "Error: WdfIoQueueCreate manual queue failed 0x%x", status);
        return status;
    }

    queueContext->WaitMaskQueue = queue;

    RingBufferInitialize(&queueContext->RingBuffer,
                            queueContext->Buffer,
                            sizeof(queueContext->Buffer));

    return status;
}


NTSTATUS
RequestCopyFromBuffer(
    _In_  WDFREQUEST        Request,
    _In_  PVOID             SourceBuffer,
    _In_  size_t            NumBytesToCopyFrom
    )
{
    NTSTATUS                status;
    WDFMEMORY               memory;

    status = WdfRequestRetrieveOutputMemory(Request, &memory);
    if( !NT_SUCCESS(status) ) {
        Trace(TRACE_LEVEL_ERROR,
            "Error: WdfRequestRetrieveOutputMemory failed 0x%x", status);
        return status;
    }

    status = WdfMemoryCopyFromBuffer(memory, 0,
                            SourceBuffer, NumBytesToCopyFrom);
    if( !NT_SUCCESS(status) ) {
        Trace(TRACE_LEVEL_ERROR,
            "Error: WdfMemoryCopyFromBuffer failed 0x%x", status);
        return status;
    }

    WdfRequestSetInformation(Request, NumBytesToCopyFrom);
    return status;
}


NTSTATUS
RequestCopyToBuffer(
    _In_  WDFREQUEST        Request,
    _In_  PVOID             DestinationBuffer,
    _In_  size_t            NumBytesToCopyTo
    )
{
    NTSTATUS                status;
    WDFMEMORY               memory;

    status = WdfRequestRetrieveInputMemory(Request, &memory);
    if( !NT_SUCCESS(status) ) {
        Trace(TRACE_LEVEL_ERROR,
            "Error: WdfRequestRetrieveInputMemory failed 0x%x", status);
        return status;
    }

    status = WdfMemoryCopyToBuffer(memory, 0,
                            DestinationBuffer, NumBytesToCopyTo);
    if( !NT_SUCCESS(status) ) {
        Trace(TRACE_LEVEL_ERROR,
            "Error: WdfMemoryCopyToBuffer failed 0x%x", status);
        return status;
    }

    return status;
}


VOID
EvtIoDeviceControl(
    _In_  WDFQUEUE          Queue,
    _In_  WDFREQUEST        Request,
    _In_  size_t            OutputBufferLength,
    _In_  size_t            InputBufferLength,
    _In_  ULONG             IoControlCode
    )
{
    NTSTATUS                status;
    PQUEUE_CONTEXT          queueContext = GetQueueContext(Queue);
    PDEVICE_CONTEXT         deviceContext = queueContext->DeviceContext;
    UNREFERENCED_PARAMETER  (OutputBufferLength);
    UNREFERENCED_PARAMETER  (InputBufferLength);

    Trace(TRACE_LEVEL_INFO,
        "EvtIoDeviceControl 0x%x", IoControlCode);

    switch (IoControlCode)
    {

    case IOCTL_SERIAL_SET_BAUD_RATE:
    {
        //
        // This is a driver for a virtual serial port. Since there is no
        // actual hardware, we just store the baud rate and don't do
        // anything with it.
        //
        SERIAL_BAUD_RATE baudRateBuffer = {0};

        status = RequestCopyToBuffer(Request,
                            &baudRateBuffer,
                            sizeof(baudRateBuffer));

        if( NT_SUCCESS(status) ) {
            SetBaudRate(deviceContext, baudRateBuffer.BaudRate);
        };
        break;
    }

    case IOCTL_SERIAL_GET_BAUD_RATE:
    {
        SERIAL_BAUD_RATE baudRateBuffer = {0};

        baudRateBuffer.BaudRate = GetBaudRate(deviceContext);

        status = RequestCopyFromBuffer(Request,
                            &baudRateBuffer,
                            sizeof(baudRateBuffer));
        break;
    }

    case IOCTL_SERIAL_SET_MODEM_CONTROL:
    {
        //
        // This is a driver for a virtual serial port. Since there is no
        // actual hardware, we just store the modem control register
        // configuration and don't do anything with it.
        //
        ULONG *modemControlRegister = GetModemControlRegisterPtr(deviceContext);

        ASSERT(modemControlRegister);

        status = RequestCopyToBuffer(Request,
                            modemControlRegister,
                            sizeof(ULONG));
        break;
    }

    case IOCTL_SERIAL_GET_MODEM_CONTROL:
    {
        ULONG *modemControlRegister = GetModemControlRegisterPtr(deviceContext);

        ASSERT(modemControlRegister);

        status = RequestCopyFromBuffer(Request,
                            modemControlRegister,
                            sizeof(ULONG));
        break;
    }

    case IOCTL_SERIAL_SET_FIFO_CONTROL:
    {
        //
        // This is a driver for a virtual serial port. Since there is no
        // actual hardware, we just store the FIFO control register
        // configuration and don't do anything with it.
        //
        ULONG *fifoControlRegister = GetFifoControlRegisterPtr(deviceContext);

        ASSERT(fifoControlRegister);

        status = RequestCopyToBuffer(Request,
                            fifoControlRegister,
                            sizeof(ULONG));
        break;
    }

    case IOCTL_SERIAL_GET_LINE_CONTROL:
    {
        status = QueueProcessGetLineControl(
                            queueContext,
                            Request);
        break;
    }


    case IOCTL_SERIAL_SET_LINE_CONTROL:
    {
        status = QueueProcessSetLineControl(
                            queueContext,
                            Request);
        break;
    }

    case IOCTL_SERIAL_GET_TIMEOUTS:
    {
        // Bug fix: previously the local was zero-initialised and
        // copied straight back, so callers (pyserial, MODE.COM) saw
        // all-zero timeouts regardless of what was set. Pull from
        // device context first.
        SERIAL_TIMEOUTS timeoutValues;

        GetTimeouts(deviceContext, &timeoutValues);
        status = RequestCopyFromBuffer(Request,
                            (void*) &timeoutValues,
                            sizeof(timeoutValues));
        break;
    }

    case IOCTL_SERIAL_SET_TIMEOUTS:
    {
        SERIAL_TIMEOUTS timeoutValues = {0};

        status = RequestCopyToBuffer(Request,
                            (void*) &timeoutValues,
                            sizeof(timeoutValues));

        if( NT_SUCCESS(status) )
        {
            if ((timeoutValues.ReadIntervalTimeout        == MAXULONG) &&
                (timeoutValues.ReadTotalTimeoutMultiplier == MAXULONG) &&
                (timeoutValues.ReadTotalTimeoutConstant   == MAXULONG))
            {
                status = STATUS_INVALID_PARAMETER;
            }
        }

        if( NT_SUCCESS(status) ) {
            SetTimeouts(deviceContext, timeoutValues);
        }

        break;
    }

    case IOCTL_SERIAL_GET_COMMSTATUS:
    {
        //
        // ClearCommError(). pyserial's flush()/in_waiting/out_waiting
        // path goes through here. Errors/HoldReasons stay zero (we
        // emulate a perfectly-clean line); AmountInInQueue is filled
        // from the PEER's ring (those are the bytes the local app can
        // read); AmountInOutQueue is always zero because EvtIoWrite
        // completes the request synchronously.
        //
        SERIAL_STATUS  serialStatus = {0};
        size_t         pendingIn = 0;
        PDEVICE_CONTEXT peer = PairAcquirePeer(deviceContext);
        if (peer != NULL && peer->QueueContext != NULL) {
            RingBufferGetAvailableData(
                            &peer->QueueContext->RingBuffer,
                            &pendingIn);
        }
        PairReleasePeer();
        serialStatus.AmountInInQueue = (ULONG)pendingIn;
        status = RequestCopyFromBuffer(Request,
                            &serialStatus,
                            sizeof(serialStatus));
        break;
    }

    case IOCTL_SERIAL_PURGE:
    {
        //
        // Real serial drivers use this to drop bytes still sitting in
        // hardware FIFOs / pending reads. For the paired-port pipe:
        //   RXCLEAR -> drop bytes the peer has produced but we have
        //              not yet read (= peer's ring buffer).
        //   TXCLEAR -> drop bytes we have produced but the peer has
        //              not yet read (= our own ring buffer).
        //   RXABORT -> cancel any reads we have parked on ReadQueue.
        //   TXABORT -> no-op; EvtIoWrite completes synchronously, so
        //              there is never a pending write to abort.
        //
        ULONG purgeMask = 0;

        status = RequestCopyToBuffer(Request,
                            &purgeMask,
                            sizeof(purgeMask));
        if (!NT_SUCCESS(status)) {
            break;
        }

        if (purgeMask & SERIAL_PURGE_TXCLEAR) {
            RingBufferReset(&queueContext->RingBuffer);
        }
        if (purgeMask & SERIAL_PURGE_RXCLEAR) {
            PDEVICE_CONTEXT peer = PairAcquirePeer(deviceContext);
            if (peer != NULL && peer->QueueContext != NULL) {
                RingBufferReset(&peer->QueueContext->RingBuffer);
            }
            PairReleasePeer();
        }
        if (purgeMask & SERIAL_PURGE_RXABORT) {
            WDFREQUEST cancelled;
            for ( ; ; ) {
                NTSTATUS s = WdfIoQueueRetrieveNextRequest(
                                    queueContext->ReadQueue,
                                    &cancelled);
                if (!NT_SUCCESS(s)) {
                    break;
                }
                WdfRequestComplete(cancelled, STATUS_CANCELLED);
            }
        }

        status = STATUS_SUCCESS;
        break;
    }

    case IOCTL_SERIAL_GET_PROPERTIES:
    {
        //
        // SERIAL_COMMPROP — describes what the port supports. Real
        // hardware reports its electrical limits here; we emulate a
        // generic RS-232 port that accepts any baud / 5-8 data bits /
        // 1, 1.5, 2 stop bits / any parity. Setting MaxBaud =
        // SERIAL_BAUD_USER tells callers "any value is fine".
        //
        SERIAL_COMMPROP commProp = {0};
        size_t          ourPending  = 0;
        size_t          peerPending = 0;
        PDEVICE_CONTEXT peer        = PairAcquirePeer(deviceContext);

        RingBufferGetAvailableData(
                            &queueContext->RingBuffer,
                            &ourPending);
        if (peer != NULL && peer->QueueContext != NULL) {
            RingBufferGetAvailableData(
                            &peer->QueueContext->RingBuffer,
                            &peerPending);
        }
        PairReleasePeer();

        commProp.PacketLength       = sizeof(SERIAL_COMMPROP);
        commProp.PacketVersion      = 2;
        commProp.ServiceMask        = SERIAL_SP_SERIALCOMM;
        commProp.MaxTxQueue         = DATA_BUFFER_SIZE;
        commProp.MaxRxQueue         = DATA_BUFFER_SIZE;
        commProp.MaxBaud            = SERIAL_BAUD_USER;
        commProp.ProvSubType        = SERIAL_PST_RS232;
        commProp.ProvCapabilities   = 0;
        commProp.SettableParams     = SERIAL_SP_BAUD
                                    | SERIAL_SP_DATABITS
                                    | SERIAL_SP_STOPBITS
                                    | SERIAL_SP_PARITY;
        commProp.SettableBaud       = SERIAL_BAUD_USER;
        commProp.SettableData       = SERIAL_DATABITS_5
                                    | SERIAL_DATABITS_6
                                    | SERIAL_DATABITS_7
                                    | SERIAL_DATABITS_8;
        commProp.SettableStopParity = SERIAL_STOPBITS_10
                                    | SERIAL_STOPBITS_15
                                    | SERIAL_STOPBITS_20
                                    | SERIAL_PARITY_NONE
                                    | SERIAL_PARITY_ODD
                                    | SERIAL_PARITY_EVEN
                                    | SERIAL_PARITY_MARK
                                    | SERIAL_PARITY_SPACE;
        commProp.CurrentTxQueue     = (ULONG)ourPending;
        commProp.CurrentRxQueue     = (ULONG)peerPending;

        status = RequestCopyFromBuffer(Request,
                            &commProp,
                            sizeof(commProp));
        break;
    }

    case IOCTL_SERIAL_WAIT_ON_MASK:
    {
        //
        // NOTE: A wait-on-mask request should not be completed until either:
        //  1) A wait event occurs; or
        //  2) A set-wait-mask request is received
        //
        // This is a driver for a virtual serial port. Since there is no
        // actual hardware, we complete the request with some failure code.
        //
        WDFREQUEST savedRequest;

        status = WdfIoQueueRetrieveNextRequest(
                            queueContext->WaitMaskQueue,
                            &savedRequest);

        if (NT_SUCCESS(status)) {
            WdfRequestComplete(savedRequest,
                            STATUS_UNSUCCESSFUL);
        }

        //
        // Keep the request in a manual queue and the framework will take
        // care of cancelling them when the app exits
        //
        status = WdfRequestForwardToIoQueue(
                            Request,
                            queueContext->WaitMaskQueue);

        if( !NT_SUCCESS(status) ) {
            Trace(TRACE_LEVEL_ERROR,
                "Error: WdfRequestForwardToIoQueue failed 0x%x", status);
            WdfRequestComplete(Request, status);
        }

        //
        // Instead of "break", use "return" to prevent the current request
        // from being completed.
        //
        return;
    }

    case IOCTL_SERIAL_SET_WAIT_MASK:
    {
        //
        // NOTE: If a wait-on-mask request is already pending when set-wait-mask
        // request is processed, the pending wait-on-event request is completed
        // with STATUS_SUCCESS and the output wait event mask is set to zero.
        //
        WDFREQUEST savedRequest;

        status = WdfIoQueueRetrieveNextRequest(
                            queueContext->WaitMaskQueue,
                            &savedRequest);

        if (NT_SUCCESS(status)) {

            ULONG eventMask = 0;
            status = RequestCopyFromBuffer(
                            savedRequest,
                            &eventMask,
                            sizeof(eventMask));

            WdfRequestComplete(savedRequest, status);
        }

        //
        // NOTE: The application expects STATUS_SUCCESS for these IOCTLs.
        //
        status = STATUS_SUCCESS;
        break;
    }

    case IOCTL_SERIAL_SET_QUEUE_SIZE:
    case IOCTL_SERIAL_SET_DTR:
    case IOCTL_SERIAL_SET_RTS:
    case IOCTL_SERIAL_CLR_RTS:
    case IOCTL_SERIAL_SET_XON:
    case IOCTL_SERIAL_SET_XOFF:
    case IOCTL_SERIAL_SET_CHARS:
    case IOCTL_SERIAL_GET_CHARS:
    case IOCTL_SERIAL_GET_HANDFLOW:
    case IOCTL_SERIAL_SET_HANDFLOW:
    case IOCTL_SERIAL_RESET_DEVICE:
        //
        // NOTE: The application expects STATUS_SUCCESS for these IOCTLs.
        //
        status = STATUS_SUCCESS;
        break;

    default:
        status = STATUS_INVALID_PARAMETER;
        break;
    }

    //
    // complete the request
    //
    WdfRequestComplete(Request, status);
}


//
// In Phase 2 the per-queue ring buffer holds *transmitted* bytes —
// data the local side has produced and which the peer side will
// later pull via EvtIoRead. The two ring buffers (one per side) thus
// form the two unidirectional pipes that make up a paired port.
//
//   Side A's EvtIoWrite -> A.RingBuffer  (TX)
//   Side B's EvtIoRead  <- A.RingBuffer  (RX of B == TX of A)
//
// After we append to our own ring we walk the PEER's ReadQueue and
// re-dispatch any read requests that were waiting because the ring
// was empty. They land back on the peer's default queue, hit
// EvtIoRead, and pull from our (now non-empty) ring.
//

VOID
EvtIoWrite(
    _In_  WDFQUEUE          Queue,
    _In_  WDFREQUEST        Request,
    _In_  size_t            Length
    )
{
    NTSTATUS                status;
    PQUEUE_CONTEXT          queueContext  = GetQueueContext(Queue);
    PDEVICE_CONTEXT         deviceContext = queueContext->DeviceContext;
    PDEVICE_CONTEXT         peerContext;
    PQUEUE_CONTEXT          peerQueue;
    WDFMEMORY               memory;
    WDFREQUEST              savedRequest;

    Trace(TRACE_LEVEL_INFO,
            "EvtIoWrite 0x%p (side %d, len %zu)",
            Request, deviceContext->Side, Length);

    status = WdfRequestRetrieveInputMemory(Request, &memory);
    if (!NT_SUCCESS(status)) {
        Trace(TRACE_LEVEL_ERROR,
            "Error: WdfRequestRetrieveInputMemory failed 0x%x", status);
        WdfRequestComplete(Request, status);
        return;
    }

    //
    // Append raw bytes to *our* ring (the peer reads them).
    //
    status = QueueProcessWriteBytes(
                            queueContext,
                            (PUCHAR)WdfMemoryGetBuffer(memory, NULL),
                            Length);
    if (!NT_SUCCESS(status)) {
        WdfRequestComplete(Request, status);
        return;
    }

    WdfRequestCompleteWithInformation(Request, STATUS_SUCCESS, Length);

    //
    // Wake any reads pending on the peer side. Hold the pair lock for
    // the entire forward-loop — the peer's WDFQUEUE handles must stay
    // alive while we iterate, and PairReleasePeer() drops the lock at
    // the end (or on the early-return below).
    //
    peerContext = PairAcquirePeer(deviceContext);
    if (peerContext == NULL || peerContext->QueueContext == NULL) {
        PairReleasePeer();
        return;
    }
    peerQueue = peerContext->QueueContext;

    for ( ; ; ) {
        status = WdfIoQueueRetrieveNextRequest(
                            peerQueue->ReadQueue,
                            &savedRequest);
        if (!NT_SUCCESS(status)) {
            break;
        }

        status = WdfRequestForwardToIoQueue(
                            savedRequest,
                            peerQueue->Queue);
        if (!NT_SUCCESS(status)) {
            Trace(TRACE_LEVEL_ERROR,
                "Error: forward-to-peer-queue failed 0x%x", status);
            WdfRequestComplete(savedRequest, status);
        }
    }

    PairReleasePeer();
}


VOID
EvtIoRead(
    _In_  WDFQUEUE          Queue,
    _In_  WDFREQUEST        Request,
    _In_  size_t            Length
    )
{
    NTSTATUS                status;
    PQUEUE_CONTEXT          queueContext  = GetQueueContext(Queue);
    PDEVICE_CONTEXT         deviceContext = queueContext->DeviceContext;
    PDEVICE_CONTEXT         peerContext;
    WDFMEMORY               memory;
    size_t                  bytesCopied = 0;

    Trace(TRACE_LEVEL_INFO,
            "EvtIoRead 0x%p (side %d, len %zu)",
            Request, deviceContext->Side, Length);

    status = WdfRequestRetrieveOutputMemory(Request, &memory);
    if (!NT_SUCCESS(status)) {
        Trace(TRACE_LEVEL_ERROR,
            "Error: WdfRequestRetrieveOutputMemory failed 0x%x", status);
        WdfRequestComplete(Request, status);
        return;
    }

    //
    // Read pulls from the PEER's ring buffer (= the other side's TX).
    // If the peer hasn't attached yet, we just queue the request and
    // wait for it to come up. Hold the pair lock across the ring read
    // so the peer can't be torn down between PairAcquirePeer and
    // RingBufferRead.
    //
    peerContext = PairAcquirePeer(deviceContext);
    if (peerContext != NULL && peerContext->QueueContext != NULL) {
        status = RingBufferRead(
                            &peerContext->QueueContext->RingBuffer,
                            (BYTE*)WdfMemoryGetBuffer(memory, NULL),
                            Length,
                            &bytesCopied);
        if (!NT_SUCCESS(status)) {
            PairReleasePeer();
            WdfRequestComplete(Request, status);
            return;
        }
    }
    PairReleasePeer();

    if (bytesCopied > 0) {
        WdfRequestCompleteWithInformation(Request, STATUS_SUCCESS, bytesCopied);
        return;
    }

    //
    // Empty peer ring (or no peer yet): park the read on our manual
    // ReadQueue. The peer's EvtIoWrite will re-dispatch us when bytes
    // arrive.
    //
    status = WdfRequestForwardToIoQueue(Request, queueContext->ReadQueue);
    if (!NT_SUCCESS(status)) {
        Trace(TRACE_LEVEL_ERROR,
            "Error: WdfRequestForwardToIoQueue failed 0x%x", status);
        WdfRequestComplete(Request, status);
    }
}


NTSTATUS
QueueProcessWriteBytes(
    _In_  PQUEUE_CONTEXT    QueueContext,
    _In_reads_bytes_(Length)
          PUCHAR            Characters,
    _In_  size_t            Length
    )
/*++

Routine Description:

    Append raw bytes to this side's TX ring buffer. Phase 1 also
    interpreted Hayes-style "AT" command sequences here and injected
    "OK"/"CONNECT" responses; Phase 2 is a transparent pipe between
    two ports, so the AT state machine has been removed.

Arguments:

    Characters - pointer to the write IRP's system buffer.
    Length     - byte count; the framework drops zero-length writes
                 before calling us.

--*/
{
    return RingBufferWrite(
                            &QueueContext->RingBuffer,
                            Characters,
                            Length);
}


NTSTATUS
QueueProcessGetLineControl(
    _In_  PQUEUE_CONTEXT    QueueContext,
    _In_  WDFREQUEST        Request
    )
{
    NTSTATUS                status;
    PDEVICE_CONTEXT         deviceContext;
    SERIAL_LINE_CONTROL     lineControl = {0};
    ULONG                   lineControlSnapshot;
    ULONG                   *lineControlRegister;

    deviceContext = QueueContext->DeviceContext;
    lineControlRegister = GetLineControlRegisterPtr(deviceContext);

    ASSERT(lineControlRegister);

    //
    // Take a snapshot of the line control register variable
    //
    lineControlSnapshot = ReadNoFence((LONG *)lineControlRegister);

    //
    // Decode the word length
    //
    if ((lineControlSnapshot & SERIAL_DATA_MASK) == SERIAL_5_DATA)
    {
        lineControl.WordLength = 5;
    }
    else if ((lineControlSnapshot & SERIAL_DATA_MASK) == SERIAL_6_DATA)
    {
        lineControl.WordLength = 6;
    }
    else if ((lineControlSnapshot & SERIAL_DATA_MASK) == SERIAL_7_DATA)
    {
        lineControl.WordLength = 7;
    }
    else if ((lineControlSnapshot & SERIAL_DATA_MASK) == SERIAL_8_DATA)
    {
        lineControl.WordLength = 8;
    }

    //
    // Decode the parity
    //
    if ((lineControlSnapshot & SERIAL_PARITY_MASK) == SERIAL_NONE_PARITY)
    {
        lineControl.Parity = NO_PARITY;
    }
    else if ((lineControlSnapshot & SERIAL_PARITY_MASK) == SERIAL_ODD_PARITY)
    {
        lineControl.Parity = ODD_PARITY;
    }
    else if ((lineControlSnapshot & SERIAL_PARITY_MASK) == SERIAL_EVEN_PARITY)
    {
        lineControl.Parity = EVEN_PARITY;
    }
    else if ((lineControlSnapshot & SERIAL_PARITY_MASK) == SERIAL_MARK_PARITY)
    {
        lineControl.Parity = MARK_PARITY;
    }
    else if ((lineControlSnapshot & SERIAL_PARITY_MASK) == SERIAL_SPACE_PARITY)
    {
        lineControl.Parity = SPACE_PARITY;
    }

    //
    // Decode the length of the stop bit
    //
    if (lineControlSnapshot & SERIAL_2_STOP)
    {
        if (lineControl.WordLength == 5)
        {
            lineControl.StopBits = STOP_BITS_1_5;
        }
        else
        {
            lineControl.StopBits = STOP_BITS_2;
        }
    }
    else
    {
        lineControl.StopBits = STOP_BIT_1;
    }

    //
    // Copy the information that was decoded to the caller's buffer
    //
    status = RequestCopyFromBuffer(Request,
                        (void*) &lineControl,
                        sizeof(lineControl));
    return status;
}


NTSTATUS
QueueProcessSetLineControl(
    _In_  PQUEUE_CONTEXT    QueueContext,
    _In_  WDFREQUEST        Request
    )
{
    NTSTATUS                status;
    PDEVICE_CONTEXT         deviceContext;
    SERIAL_LINE_CONTROL     lineControl = {0};
    ULONG                   *lineControlRegister;
    UCHAR                   lineControlData = 0;
    UCHAR                   lineControlStop = 0;
    UCHAR                   lineControlParity = 0;
    ULONG                   lineControlSnapshot;
    ULONG                   lineControlNew;
    ULONG                   lineControlPrevious;
    ULONG                   i;

    deviceContext = QueueContext->DeviceContext;
    lineControlRegister = GetLineControlRegisterPtr(deviceContext);

    ASSERT(lineControlRegister);

    //
    // This is a driver for a virtual serial port. Since there is no
    // actual hardware, we just store the line control register
    // configuration and don't do anything with it.
    //
    status = RequestCopyToBuffer(Request,
                        (void*) &lineControl,
                        sizeof(lineControl));

    //
    // Bits 0 and 1 of the line control register
    //
    if( NT_SUCCESS(status) )
    {
        switch (lineControl.WordLength)
        {
        case 5:
            lineControlData = SERIAL_5_DATA;
            SetValidDataMask(deviceContext, 0x1f);
            break;

        case 6:
            lineControlData = SERIAL_6_DATA;
            SetValidDataMask(deviceContext, 0x3f);
            break;

        case 7:
            lineControlData = SERIAL_7_DATA;
            SetValidDataMask(deviceContext, 0x7f);
            break;

        case 8:
            lineControlData = SERIAL_8_DATA;
            SetValidDataMask(deviceContext, 0xff);
            break;

        default:
            status = STATUS_INVALID_PARAMETER;
            break;
        }
    }

    //
    // Bit 2 of the line control register
    //
    if( NT_SUCCESS(status) )
    {
        switch (lineControl.StopBits)
        {
        case STOP_BIT_1:
            lineControlStop = SERIAL_1_STOP;
            break;

        case STOP_BITS_1_5:
            if (lineControlData != SERIAL_5_DATA)
            {
                status = STATUS_INVALID_PARAMETER;
                break;
            }
            lineControlStop = SERIAL_1_5_STOP;
            break;

        case STOP_BITS_2:
            if (lineControlData == SERIAL_5_DATA)
            {
                status = STATUS_INVALID_PARAMETER;
                break;
            }
            lineControlStop = SERIAL_2_STOP;
            break;

        default:
            status = STATUS_INVALID_PARAMETER;
            break;
        }
    }

    //
    // Bits 3, 4 and 5 of the line control register
    //
    if( NT_SUCCESS(status) )
    {
        switch (lineControl.Parity)
        {
        case NO_PARITY:
            lineControlParity = SERIAL_NONE_PARITY;
            break;

        case EVEN_PARITY:
            lineControlParity = SERIAL_EVEN_PARITY;
            break;

        case ODD_PARITY:
            lineControlParity = SERIAL_ODD_PARITY;
            break;

        case SPACE_PARITY:
            lineControlParity = SERIAL_SPACE_PARITY;
            break;

        case MARK_PARITY:
            lineControlParity = SERIAL_MARK_PARITY;
            break;

        default:
            status = STATUS_INVALID_PARAMETER;
            break;
        }
    }

    //
    // Update our line control register variable atomically
    //
    i=0;
    do {
        i++;
        if ((i & 0xf) == 0) {
            //
            // We've been spinning in a loop for a while trying to
            // update the line control register variable atomically.
            // Yield the CPU for other threads for a while.
            //
#ifdef _KERNEL_MODE
            LARGE_INTEGER   interval;
            interval.QuadPart = 0;
            KeDelayExecutionThread(UserMode, FALSE, &interval);
#else
            SwitchToThread();
#endif
        }

        lineControlSnapshot = ReadNoFence((LONG *)lineControlRegister);

        lineControlNew = (lineControlSnapshot & SERIAL_LCR_BREAK) |
                        (lineControlData | lineControlParity | lineControlStop);

        lineControlPrevious = InterlockedCompareExchange(
                      (LONG *) lineControlRegister,
                       lineControlNew,
                       lineControlSnapshot);

    } while (lineControlPrevious != lineControlSnapshot);

    return status;
}
