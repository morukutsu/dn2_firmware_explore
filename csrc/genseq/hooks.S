    .text

    .global gseq_note_hook
gseq_note_hook:
    /* Original arguments 1 and 2 are the track and playback copy.
     * d4 can already hold a sound-lock offset at this point. */
    lea     56(%sp),%a0
    move.l  (%a0),-(%sp)
    move.l  4(%a0),-(%sp)
    move.l  %a3,-(%sp)
    jsr     gseq_mark_note
    lea     12(%sp),%sp
    move.l  %fp,84(%a3)
    move.l  %a3,%d0
    jmp     0x400d8ae8

    .global gseq_admit_hook
gseq_admit_hook:
    move.l  32(%a0),%d0
    cmpi.l  #-2,%d0
    bne.s   1f
    move.l  %a1,-(%sp)
    movea.l -44(%fp),%a1
    move.l  4(%a1),-(%sp)
    move.l  %a0,-(%sp)
    jsr     gseq_admit
    movea.l (%sp)+,%a0
    addq.l  #4,%sp
    movea.l (%sp)+,%a1
    tst.l   %d0
    beq.s   3f
    move.l  32(%a0),%d0
1:  tst.l   %d0
    bmi.s   2f
    jmp     0x4002656c
2:  jmp     0x4002657c
3:  jmp     0x40026e50

    .global gseq_stop_hook
gseq_stop_hook:
    addq.l  #1,gseq_transport
    addq.l  #1,gseq_history_restart
    clr.l   0x42c5a9d0
    jmp     0x400d9684

    .global gseq_reset_clock_hook
gseq_reset_clock_hook:
    addq.l  #1,gseq_transport
    addq.l  #1,gseq_history_restart
    clr.l   0x42c5a9d0
    jmp     0x400d83f2

    .global gseq_frame_hook
gseq_frame_hook:
    tst.l   gseq_anchor_count
    bne.s   2f
    move.l  0x42c5a9a0,%d0
    cmp.l   gseq_history_copy,%d0
    bne.s   2f
    move.l  0x42c5a918,%d0
    cmp.l   gseq_history_running,%d0
    beq.s   1f
2:
    lea     -16(%sp),%sp
    movem.l %d0-%d1/%a0-%a1,(%sp)
    move.l  -48(%fp),%d0
    subq.l  #1,%d0
    move.l  %d0,-(%sp)
    jsr     gseq_frame_clock
#ifdef GSEQ_PERFORMANCE_PROBE
    .global gseq_probe_clock_done
gseq_probe_clock_done:
#endif
    addq.l  #4,%sp
    movem.l (%sp),%d0-%d1/%a0-%a1
    lea     16(%sp),%sp
1:  move.l  0x4058e554,%d0
    jmp     0x40027b6e

    .global gseq_stock_queue_patterns
gseq_stock_queue_patterns:
    lea     -20(%sp),%sp
    movem.l %d2-%d4/%a2-%a3,(%sp)
    jmp     0x400d93f8

    .global gseq_prepare_hook
gseq_prepare_hook:
    move.l  #0x1de6a,%d0
    lea     44(%sp),%a0
    move.l  (%a0),0(%a2,%d0.l)
    move.l  %a2,-(%sp)
    jsr     gseq_prepare_copy
    addq.l  #4,%sp
    jmp     0x400de708

    .global gseq_show
gseq_show:
    clr.l   gseq_editor_view
    link.w  %fp,#-40
    movem.l %d2-%d4/%a2-%a4,(%sp)
    jmp     0x4005e7ec

    .global gseq_project_hook
gseq_project_hook:
#ifdef GSEQ_PERSISTENCE_PROBE
    jmp     gseq_load_project
    .global gseq_stock_load_project
gseq_stock_load_project:
    lea     -36(%sp),%sp
    movem.l %d2-%d5/%a2-%fp,(%sp)
    jmp     0x400e178a

    .global gseq_stock_save_project
gseq_stock_save_project:
    lea     -44(%sp),%sp
    movem.l %d2-%d7/%a2-%fp,(%sp)
    jmp     0x400e149c

    .global gseq_create_hook
gseq_create_hook:
    jsr     gseq_new_project
    jmp     0x4004296e

    .global gseq_stock_pattern_copy
gseq_stock_pattern_copy:
    link.w  %fp,#-40
    movem.l %d2-%d4/%a2-%a4,(%sp)
    jmp     0x40043b9a

    .global gseq_stock_pattern_clear
gseq_stock_pattern_clear:
    link.w  %fp,#-56
    lea     -16(%fp),%a0
    jmp     0x400464a8

    .global gseq_stock_pattern_paste
gseq_stock_pattern_paste:
    link.w  %fp,#-64
    lea     -24(%fp),%a0
    jmp     0x400466f6

    .global gseq_stock_pattern_undo
gseq_stock_pattern_undo:
    link.w  %fp,#-52
    lea     -8(%fp),%a0
    jmp     0x4004070a

    .global gseq_stock_reload_pattern
gseq_stock_reload_pattern:
    link.w  %fp,#-32
    movem.l %d2-%d5/%a2-%a3,(%sp)
    jmp     0x400443c4
#else
    lea     -36(%sp),%sp
    movem.l %d2-%d5/%a2-%fp,(%sp)
    jsr     gseq_reset
    jmp     0x400e178a
#endif

    .global gseq_stock_produce
gseq_stock_produce:
    lea     -52(%sp),%sp
    movem.l %d2-%d7/%a2-%fp,(%sp)
    jmp     0x400d865c

    .global gseq_stock_enqueue
gseq_stock_enqueue:
    lea     -20(%sp),%sp
    movem.l %d2-%d4/%a2-%a3,(%sp)
    jmp     0x40138b64

    .section .note.GNU-stack,"",@progbits
