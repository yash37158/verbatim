import { notFound } from "next/navigation";
import { getSpace, listConversations, listDocuments, listMessages } from "@/lib/api";
import { Workspace } from "@/components/Workspace";

export default async function SpacePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ c?: string }>;
}) {
  const [{ id }, { c }] = await Promise.all([params, searchParams]);
  const [space, documents, conversations] = await Promise.all([
    getSpace(id),
    listDocuments(id),
    listConversations(id),
  ]);
  if (!space) notFound();

  // `?c=` deep-links a conversation. Preloading its messages here, rather than in the
  // browser after mount, means a reload restores the thread with no flash of empty state.
  const active = conversations.find((x) => x.id === c) ?? null;
  const initialMessages = active ? await listMessages(active.id) : [];

  return (
    <Workspace
      space={space}
      documents={documents}
      conversations={conversations}
      activeConversation={active}
      initialMessages={initialMessages}
    />
  );
}
