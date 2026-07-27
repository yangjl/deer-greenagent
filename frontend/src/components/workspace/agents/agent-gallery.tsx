"use client";

import { BotIcon, PlusIcon } from "lucide-react";
import { useRouter } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAgentInventory } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";

import { AgentCard } from "./agent-card";

export function AgentGallery() {
  const { t } = useI18n();
  const { items, isLoading, error } = useAgentInventory();
  const router = useRouter();
  const builtinItems = items.filter((item) => item.origin === "builtin");
  const customItems = items.filter((item) => item.origin === "custom");

  const handleNewAgent = () => {
    router.push("/workspace/agents/new");
  };

  return (
    <div className="flex size-full flex-col">
      {/* Page header */}
      <div className="flex items-center justify-between border-b px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold">{t.agents.title}</h1>
          <p className="text-muted-foreground mt-0.5 text-sm">
            {t.agents.description}
          </p>
        </div>
        <Button onClick={handleNewAgent}>
          <PlusIcon className="mr-1.5 h-4 w-4" />
          {t.agents.newAgent}
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-5">
        {isLoading ? (
          <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
            {t.common.loading}
          </div>
        ) : error ? (
          <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
            {t.agents.inventoryError}
          </div>
        ) : (
          <Tabs defaultValue="builtin" className="h-full gap-5">
            <TabsList variant="line" aria-label={t.agents.title}>
              <TabsTrigger value="builtin">
                {t.agents.builtinTab}
                <Badge variant="secondary" className="ml-1 tabular-nums">
                  {builtinItems.length}
                </Badge>
              </TabsTrigger>
              <TabsTrigger value="custom">
                {t.agents.customTab}
                <Badge variant="secondary" className="ml-1 tabular-nums">
                  {customItems.length}
                </Badge>
              </TabsTrigger>
            </TabsList>

            <TabsContent value="builtin" className="space-y-4">
              <p className="text-muted-foreground text-sm">
                {t.agents.builtinDescription}
              </p>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {builtinItems.map((agent) => (
                  <AgentCard
                    key={`${agent.kind}:${agent.name}`}
                    agent={agent}
                  />
                ))}
              </div>
            </TabsContent>

            <TabsContent value="custom" className="space-y-4">
              <p className="text-muted-foreground text-sm">
                {t.agents.customDescription}
              </p>
              {customItems.length === 0 ? (
                <div className="flex h-56 flex-col items-center justify-center gap-3 text-center">
                  <div className="bg-muted flex h-14 w-14 items-center justify-center rounded-full">
                    <BotIcon className="text-muted-foreground h-7 w-7" />
                  </div>
                  <div>
                    <p className="font-medium">{t.agents.emptyTitle}</p>
                    <p className="text-muted-foreground mt-1 text-sm">
                      {t.agents.emptyDescription}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    className="mt-2"
                    onClick={handleNewAgent}
                  >
                    <PlusIcon className="mr-1.5 h-4 w-4" />
                    {t.agents.newAgent}
                  </Button>
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {customItems.map((agent) => (
                    <AgentCard
                      key={`${agent.kind}:${agent.name}`}
                      agent={agent}
                    />
                  ))}
                </div>
              )}
            </TabsContent>
          </Tabs>
        )}
      </div>
    </div>
  );
}
