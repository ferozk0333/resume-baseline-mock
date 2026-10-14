import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export default function RecruiterPage() {
  return (
    <Card className="mx-auto max-w-xl">
      <CardHeader>
        <CardTitle>Recruiter view</CardTitle>
        <CardDescription>Coming in the next phase.</CardDescription>
      </CardHeader>
      <CardContent className="text-sm text-muted-foreground">
        Jobs, ranked applicants and the profile panel will appear here.
      </CardContent>
    </Card>
  );
}
