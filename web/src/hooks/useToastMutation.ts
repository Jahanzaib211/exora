import { useMutation, useQueryClient, type UseMutationResult, type UseMutationOptions } from "@tanstack/react-query";
import { App } from "antd";
import { useCallback } from "react";
import { ApiError } from "../api";
import { invalidate as applyInvalidate } from "../lib/query-keys";

/**
 * A `useMutation` that always tells the user what happened.
 *
 * Roughly half the mutations in the codebase had an `onError` toast and no success
 * toast, so a successful edit gave no confirmation at all; a few had neither. This
 * makes feedback the default rather than something each page remembers.
 *
 * It also accepts an invalidation bundle from `lib/query-keys` so a write and the
 * cache fan-out it implies stay in one place.
 */

export type ToastMutationOptions<TData, TVars, TContext = unknown> = UseMutationOptions<TData, Error, TVars, TContext> & {
  /** One of the `INVALIDATE.*` bundles. */
  invalidates?: readonly (readonly unknown[])[];
  /** Set false for silent background writes. */
  successMessage?: string | false;
  /** Defaults to the error message; set to customise. */
  errorMessage?: string;
};

export function useToastMutation<TData = unknown, TVars = void, TContext = unknown>(
  mutationFn: (vars: TVars) => Promise<TData>,
  options: ToastMutationOptions<TData, TVars, TContext> = {},
): UseMutationResult<TData, Error, TVars, TContext> {
  const { message } = App.useApp();
  const qc = useQueryClient();
  const { invalidates, successMessage, errorMessage, onSuccess, onError, onSettled, ...rest } = options;

  const afterSettled = useCallback(
    (...args: Parameters<NonNullable<typeof onSettled>>) => {
      if (invalidates?.length) applyInvalidate(qc, invalidates);
      onSettled?.(...args);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [qc, invalidates, onSettled],
  );

  return useMutation({
    mutationFn,
    ...rest,
    onSuccess: (data, vars, ctx, meta) => {
      if (successMessage) void message.success(successMessage);
      (onSuccess as ((...a: unknown[]) => void) | undefined)?.(data, vars, ctx, meta);
    },
    onError: (error, vars, ctx, meta) => {
      // 401 is handled globally by the API client (it redirects to /login).
      if (!(error instanceof ApiError && error.status === 401)) {
        void message.error(errorMessage ? `${errorMessage}: ${error.message}` : error.message);
      }
      (onError as ((...a: unknown[]) => void) | undefined)?.(error, vars, ctx, meta);
    },
    onSettled: afterSettled,
  });
}

/** Imperative one-off confirmation, for destructive actions that need a second click. */
export function useConfirmAction() {
  const { modal } = App.useApp();
  return useCallback(
    (opts: { title: string; content?: React.ReactNode; okText?: string; okButtonProps?: { danger?: boolean }; onOk: () => void | Promise<void> }) =>
      modal.confirm({
        title: opts.title,
        content: opts.content,
        okText: opts.okText ?? "Confirm",
        cancelText: "Cancel",
        okButtonProps: opts.okButtonProps ?? { danger: true },
        onOk: opts.onOk,
      }),
    [modal],
  );
}