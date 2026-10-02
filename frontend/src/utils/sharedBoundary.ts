/**
 * What a shared-boundary pair says in place of a figure.
 *
 * Its gap and overlap probability are both exactly zero BY CONSTRUCTION — one
 * phase's end IS the next one's start — and `none` is the wrong word for the
 * second, meaning "separated at 2 sigma" everywhere else here. The CSV and PDF
 * print the same phrase (intervals.SHARED_BOUNDARY_TEXT). Only analyses run
 * before ordered pairs were dropped from the differences carry such a pair.
 *
 * Its own module so the exports can name it without loading the panel, and
 * with it the plotting library.
 */
export const SHARED_BOUNDARY_TEXT = 'shares a transition'
